"""Additive upgrades use synthetic bytes and the ordinary sparse planner."""
from copy import deepcopy
import tempfile
from pathlib import Path
import subprocess
import sys
import unittest

from test_operations import sparse_fixture, Backend
from a6700_unlock.catalog import Catalog, CatalogError
from a6700_unlock.device_profile import DeviceProfile, inspect_sectors
from a6700_unlock.feature_updates import plan_update
from a6700_unlock.planner import analyze
from a6700_unlock.operations import _execute_session


def update_fixture():
    original, sectors = sparse_fixture(schema=2)
    data = deepcopy(original.catalog.data)
    data['features'][0].update(revision=2, updates=[dict(id='a-improvement', revision=2,
        edit_ids=['update-app', 'update-wbi'])])
    for image, value in [('app', '03'), ('wbi', '04')]:
        data['edits'].append(dict(id='update-'+image, image=image, offset=42, disabled='00',
            enabled=value, known_values=['00', value], feature='a', purpose='synthetic improvement'))
    catalog = Catalog.from_dict(data)
    profile = deepcopy(original.data);profile['catalog_sha256'] = catalog.digest
    for row in profile['entries']:
        if row['role'] == 'target' and row['file_offset'] == 0:
            row['edit_ids'].append('update-'+row['image'])
    return DeviceProfile.from_dict(profile, catalog), sectors


def replace_edits(profile, sectors, values):
    result = [bytearray(s) for s in sectors]
    index = {(r['image'], r['file_offset']): r['index'] for r in profile.entries if r['role']=='target'}
    for edit in profile.catalog.edits:
        if edit.id not in values:continue
        value = values[edit.id]
        for at, byte in zip(range(edit.offset, edit.end), value):
            result[index[edit.image, at//512*512]][at%512] = byte
    return list(map(bytes, result))


class FeatureUpdateTests(unittest.TestCase):
    def setUp(self):
        self.profile, self.disabled = update_fixture()
        self.old = replace_edits(self.profile, self.disabled,
            {e.id:e.enabled for e in self.profile.catalog.edits if not e.id.startswith('update-')})

    def feature(self, sectors):
        plan, _ = inspect_sectors(self.profile, sectors)
        return plan['features'][0]

    def test_complete_old_install_is_enabled_with_update_available(self):
        row = self.feature(self.old)
        self.assertEqual((row['state'], row['installed_revision'], row['target_revision'], row['update_state']),
                         ('enabled',1,2,'available'))
        self.assertTrue(row['update_available'])
        self.assertEqual(set(row['update_edit_ids']), {'update-app','update-wbi'})

    def test_upgrade_changes_only_delta_and_is_idempotent(self):
        plan, after = plan_update(self.profile, self.old, 'a')
        self.assertEqual((plan['status'],plan['changed_bytes'],plan['changed_sectors']),('ready',2,2))
        self.assertEqual(plan['requested_action'],'update')
        changes = [(i,j) for i,(a,b) in enumerate(zip(self.old,after)) for j,(x,y) in enumerate(zip(a,b)) if x!=y]
        self.assertEqual(changes,[(0,42),(2,42)])
        self.assertEqual(self.feature(after)['update_state'],'current')
        repeated, again = plan_update(self.profile,after,'a')
        self.assertEqual((repeated['status'],repeated['changed_bytes']),('no-op',0))
        self.assertEqual(again,after)

    def test_first_unlock_includes_update_but_update_does_not_unlock_base(self):
        row=self.feature(self.disabled)
        self.assertEqual((row['state'],row['installed_revision'],row['update_state']),('disabled',0,'uninstalled'))
        blocked, after=plan_update(self.profile,self.disabled,'a')
        self.assertFalse(blocked['compatible']);self.assertEqual(after,self.disabled)
        _, latest=inspect_sectors(self.profile,self.disabled,'unlock',features=['a'])
        self.assertEqual(self.feature(latest)['installed_revision'],2)

    def test_known_interrupted_update_completes_one_remaining_byte(self):
        partial=replace_edits(self.profile,self.old,{'update-app':b'\x03'})
        self.assertEqual(self.feature(partial)['update_state'],'incomplete')
        plan, after=plan_update(self.profile,partial,'a')
        self.assertEqual(plan['changed_bytes'],1)
        self.assertEqual(self.feature(after)['update_state'],'current')

    def test_partial_base_is_not_misclassified_as_old_version(self):
        partial=replace_edits(self.profile,self.old,{'a-wbi':b'\0'})
        self.assertEqual(self.feature(partial)['update_state'],'unrecognized')
        plan,after=plan_update(self.profile,partial,'a')
        self.assertEqual(plan['status'],'blocked');self.assertEqual(after,partial)

    def test_unknown_delta_or_unmanaged_byte_stops_without_proposal(self):
        for target in ('field','unmanaged','guard'):
            with self.subTest(target=target):
                bad=list(self.old)
                index,at=(0,42) if target=='field' else (0,100) if target=='unmanaged' else (3,5)
                row=bytearray(bad[index]);row[at]=0x99;bad[index]=bytes(row)
                plan,after=plan_update(self.profile,bad,'a')
                self.assertFalse(plan['compatible']);self.assertEqual(after,bad)
                self.assertEqual(plan['changed_bytes'],0)

    def test_disabled_feature_cannot_update(self):
        data=deepcopy(self.profile.catalog.data)
        data['features'][0].update(available=False,unavailable_reason='not released')
        catalog=Catalog.from_dict(data);p=deepcopy(self.profile.data);p['catalog_sha256']=catalog.digest
        with self.assertRaises(ValueError):plan_update(DeviceProfile.from_dict(p,catalog),self.old,'a')

    def test_close_clears_base_and_delta_but_preserves_other_feature(self):
        _,latest=plan_update(self.profile,self.old,'a')
        _,closed=inspect_sectors(self.profile,latest,'close',features=['a'])
        self.assertEqual(self.feature(closed)['update_state'],'uninstalled')
        self.assertEqual(closed[0][15],self.old[0][15])
        self.assertEqual(closed[1],self.old[1])

    def test_offline_image_and_sparse_inspection_agree(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name)
            app=self.old[0]+self.old[1]+bytes(512);wbi=self.old[2]+bytes(512)
            paths={key:folder/key for key in ('app','wbi')}
            paths['app'].write_bytes(app);paths['wbi'].write_bytes(wbi)
            plan=analyze(self.profile.catalog,paths)
            self.assertTrue(plan['compatible'])
            self.assertEqual(plan['features'][0]['update_state'],'available')

    def test_invalid_revision_metadata_is_rejected(self):
        changes=[lambda f:f.update(revision=True),lambda f:f.update(revision=3),
                 lambda f:f['updates'][0].update(revision=1),
                 lambda f:f['updates'][0].update(edit_ids=['missing']),
                 lambda f:f['updates'][0].update(edit_ids=['b-app']),
                 lambda f:f['updates'][0].update(edit_ids=['update-app','update-app']),
                 lambda f:f['updates'][0].update(edit_ids=['neutral'])]
        for change in changes:
            with self.subTest(change=change):
                data=deepcopy(self.profile.catalog.data);change(data['features'][0])
                with self.assertRaises(CatalogError):Catalog.from_dict(data)

    def test_catalog_without_updates_keeps_legacy_behavior(self):
        profile,sectors=sparse_fixture(schema=2)
        plan,_=inspect_sectors(profile,sectors)
        self.assertNotIn('update_state',plan['features'][0])
        with self.assertRaises(ValueError):plan_update(profile,sectors,'a')

    def test_later_revision_recognizes_a_complete_prior_revision(self):
        _,revision_two=plan_update(self.profile,self.old,'a')
        data=deepcopy(self.profile.catalog.data)
        data['features'][0]['revision']=3
        data['features'][0]['updates'].append(dict(id='next',revision=3,edit_ids=['update-three']))
        data['edits'].append(dict(id='update-three',image='app',offset=43,disabled='00',enabled='01',
            known_values=['00','01'],feature='a',purpose='next synthetic improvement'))
        catalog=Catalog.from_dict(data);p=deepcopy(self.profile.data);p['catalog_sha256']=catalog.digest
        p['entries'][0]['edit_ids'].append('update-three')
        profile=DeviceProfile.from_dict(p,catalog)
        plan,after=plan_update(profile,revision_two,'a');row=plan['features'][0]
        self.assertEqual((row['installed_revision'],row['update_state'],plan['changed_bytes']),(2,'available',1))
        self.assertEqual(after[0][43],1)

    def test_candidate_build_requires_explicit_output_before_reading_inputs(self):
        script=Path(__file__).resolve().parents[1]/'scripts/build_native.py'
        result=subprocess.run([sys.executable,'-B',str(script),'--catalog','missing.json','--profile','missing.json'],
                              capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertIn('require --output-dir',result.stderr)

    def test_candidate_build_refuses_bundled_output(self):
        root=Path(__file__).resolve().parents[1]
        resource=root/'src/a6700_unlock/data'
        before=(resource/'native/manifest.json').read_bytes()
        result=subprocess.run([sys.executable,'-B',str(root/'scripts/build_native.py'),
            '--catalog',str(resource/'a6700-2.00.json'),'--profile',str(resource/'device-profile.json'),
            '--output-dir',str(resource/'native'),'--clang',sys.executable,'--linker',sys.executable],
            capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertIn('must not overwrite bundled',result.stderr)
        self.assertEqual((resource/'native/manifest.json').read_bytes(),before)

    def transaction(self, before, failure=None):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name);backend=Backend(before,folder,failure)
            result=_execute_session(self.profile,backend,'update',folder,lambda text:None,features=['a'])
            return result,backend

    def test_update_transaction_reads_fresh_state_and_independently_verifies(self):
        result,backend=self.transaction(self.old)
        self.assertEqual((result['status'],backend.writes,backend.reads),('completed',1,2))
        self.assertEqual(result['plan']['changed_bytes'],2)
        self.assertEqual(result['final_features'][0]['update_state'],'current')

    def test_update_transaction_does_not_unlock_a_freshly_observed_disabled_base(self):
        result,backend=self.transaction(self.disabled)
        self.assertEqual((result['status'],backend.writes),('blocked',0))
        self.assertEqual(backend.sectors,self.disabled)

    def test_update_failure_returns_to_old_enabled_revision(self):
        result,backend=self.transaction(self.old,'rollback')
        self.assertEqual(result['status'],'rolled-back')
        self.assertEqual(backend.sectors,self.old)
        self.assertEqual(self.feature(backend.sectors)['installed_revision'],1)

    def test_update_lost_reply_is_uncertain_and_not_resent(self):
        result,backend=self.transaction(self.old,'lost-reply')
        self.assertEqual((result['status'],backend.writes),('uncertain',1))
        self.assertFalse(result['verified'])
