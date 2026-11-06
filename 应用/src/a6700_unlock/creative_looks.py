"""Independent Creative Look options and package planning; no device access.

These options belong to a startup package, not the fixed-sector catalogue.
The public USB backend does not yet install that package. Synthetic desktop
data exercises selection only and must never authorize a physical write.
"""
from __future__ import annotations

from dataclasses import dataclass


FEATURES = (
    dict(id='fl23', name='FL2／FL3 创意外观', requires=[],
         description='新增独立 FL2／FL3、图标及完整说明，可保留原有名称显示。'),
    dict(id='creative_look_names', name='创意外观中文名称', requires=[],
         description='主列表和编号 1～6 显示完整名称；不要求启用 FL2／FL3。'),
)
FEATURE_IDS = tuple(row['id'] for row in FEATURES)
PREPARING = 'FL2／FL3 实机安装组件缺失或校验未通过，请使用本版完整应用。'


def feature_definitions(catalog, *, simulation=False, fl23_available=False):
    """Add package options without changing the bound fixed-sector catalogue."""
    rows = [dict(row) for row in catalog.data['features']]
    existing = {row['id'] for row in rows}
    for feature in FEATURES:
        if not simulation and feature['id']=='creative_look_names':
            continue  # Deferred from this release; no physical placeholder.
        if feature['id'] not in existing:
            available=simulation or (feature['id']=='fl23' and fl23_available)
            rows.append(dict(feature, available=available,
                             **({} if available else {'unavailable_reason': PREPARING})))
    return [row for row in rows if row['id']!='creative_look_names']


def pending_rows(*, fl23_available=False):
    # An old sparse inspection has no evidence about these options. In
    # particular, do not call an already modified camera "not installed".
    return [dict(feature, state='unknown', available=fl23_available,
                 **({} if fl23_available else {'unavailable_reason': PREPARING}))
            for feature in FEATURES if feature['id']=='fl23']


@dataclass(frozen=True)
class LookOptions:
    fl23: bool = False
    creative_look_names: bool = False

    def __post_init__(self):
        if type(self.fl23) is not bool or type(self.creative_look_names) is not bool:
            raise ValueError('Creative Look options must be booleans')

    @property
    def flags(self):
        return int(self.fl23) | (int(self.creative_look_names) << 1)

    @classmethod
    def from_flags(cls, flags):
        if type(flags) is not int or not 0 <= flags <= 3:
            raise ValueError('Unknown Creative Look option flags')
        return cls(bool(flags & 1), bool(flags & 2))

    @property
    def components(self):
        components = []
        if self.flags:
            components.append('startup-loader')
        if self.fl23:
            components.extend(('fl23-cp-support', 'fl23-ap-model', 'fl23-icons', 'fl23-full-help'))
        if self.creative_look_names:
            components.append('localized-look-names')
        return tuple(components)


def plan_options(current, selected, action, *, main_selection):
    """Plan partial selection while retaining every unselected option.

The caller must supply freshly observed flags and main selection. This is
only a logical plan: it contains no addresses, payloads or write permission.
Closing FL23 normalizes a selected FL2/FL3 to ST before removing its model.
"""
    if not isinstance(current, LookOptions):
        raise ValueError('Fresh Creative Look option state is required')
    if action not in ('unlock', 'close'):
        raise ValueError('Unsupported Creative Look action')
    selected = tuple(selected)
    if not selected or len(selected) != len(set(selected)) or not set(selected) <= set(FEATURE_IDS):
        raise ValueError('Select known, unique Creative Look options')
    # 16 is the native placeholder, not a usable main choice.
    if type(main_selection) is not int or main_selection not in (*range(16), 17, 18):
        raise ValueError('Unknown current Creative Look selection')
    if not current.fl23 and main_selection in (17, 18):
        raise ValueError('Current selection and installed option state disagree')
    target = LookOptions(**{key: action == 'unlock' if key in selected else getattr(current, key)
                            for key in FEATURE_IDS})
    cp_action = ('preserve' if target.fl23 == current.fl23 else
                 'install' if target.fl23 else 'restore')
    return dict(schema_version=1, kind='creative-look-package-plan', executable=False,
                before_flags=current.flags, after_flags=target.flags,
                selected_features=[key for key in FEATURE_IDS if key in selected],
                changed_features=[key for key in FEATURE_IDS if getattr(current, key) != getattr(target, key)],
                add_components=sorted(set(target.components) - set(current.components)),
                remove_components=sorted(set(current.components) - set(target.components)),
                cp_action=cp_action, cp_preflight_required=cp_action != 'preserve',
                main_selection_before=main_selection,
                main_selection_after=(0 if not target.fl23 and main_selection in (17, 18)
                                      else main_selection),
                shared_loader_required=bool(target.flags),
                captions='localized-full' if target.creative_look_names else 'native-short',
                fl23_help='full-native' if target.fl23 else None)
