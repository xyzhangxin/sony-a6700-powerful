"""Read three ordered original JPEGs; compare matching object regions offline.

Regions are specified manually in a JSON file because the camera moved. Values
are decoded, encoded JPEG RGB samples, not linear-light or colorimetric values.
This checks conspicuous channel reversal, not pixel equality or CP causation.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform

import numpy as np
from PIL import Image, ImageDraw, ImageFont, __version__ as pillow_version


TAGS = {
    271: 'Make', 272: 'Model', 274: 'Orientation', 305: 'Software',
    33434: 'ExposureTime', 33437: 'FNumber', 34850: 'ExposureProgram',
    34855: 'ISO',
    37380: 'ExposureBiasValue', 37386: 'FocalLength',
    40961: 'ColorSpace',
    41986: 'ExposureMode', 41987: 'WhiteBalance', 42036: 'LensModel',
}
FIXED = ('ExposureTime', 'FNumber', 'ISO', 'ExposureProgram',
         'ExposureMode', 'WhiteBalance', 'FocalLength', 'ColorSpace')
LABELS = ('A1 / IDENTITY17', 'B / RBSWAP17', 'A2 / IDENTITY17')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metadata(im):
    exif = im.getexif()
    tags = dict(exif)
    if 34665 in exif:
        sub = exif.get_ifd(34665)
        tags.update(sub)
        if 40965 in sub:
            tags['interoperability'] = exif.get_ifd(40965)
    result = {}
    for tag, name in TAGS.items():
        value = tags.get(tag)
        if value is not None:
            result[name] = value if isinstance(value, (str, int, float)) else str(value)
    interop = tags.get('interoperability', {})
    result['InteroperabilityIndex'] = str(interop.get(1, 'unknown'))
    icc = im.info.get('icc_profile')
    result['embedded_icc_sha256'] = hashlib.sha256(icc).hexdigest() if icc else None
    return result


def region_stats(im, box):
    a = np.asarray(im.crop(box), dtype=np.float32).reshape(-1, 3)
    median = np.median(a, axis=0)
    mean = a.mean(axis=0)
    den = a[:, 2] + a[:, 0]
    chroma = np.divide(a[:, 2] - a[:, 0], den, out=np.zeros_like(den), where=den > 0)
    return {
        'box_native_pixels': list(box), 'pixels': len(a),
        'median_RGB_0_255': median.tolist(), 'mean_RGB_0_255': mean.tolist(),
        'p10_RGB_0_255': np.percentile(a, 10, axis=0).tolist(),
        'p90_RGB_0_255': np.percentile(a, 90, axis=0).tolist(),
        'fraction_B_gt_R': float(np.mean(a[:, 2] > a[:, 0])),
        'median_B_minus_R': float(np.median(a[:, 2] - a[:, 0])),
        'median_blue_red_chromaticity': float(np.median(chroma)),
        'median_RGB_B_over_R': float(median[2] / median[0]) if median[0] else None,
        'zero_channel_fraction': float(np.mean(a == 0)),
        'saturated_channel_fraction': float(np.mean(a == 255)),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('jpegs', type=Path, nargs=3, help='A1, B, A2 in shooting order')
    p.add_argument('--rois', type=Path, required=True)
    p.add_argument('--out-dir', type=Path, required=True)
    p.add_argument('--region', help='Named region to show in the comparison image')
    args = p.parse_args()
    if args.out_dir.exists():
        p.error('Output directory already exists')
    rois = json.loads(args.rois.read_text(encoding='utf-8'))
    files, images = [], []
    for label, path in zip(LABELS, args.jpegs):
        with Image.open(path) as test:
            test.verify()
        with Image.open(path) as im:
            im.seek(0)  # Sony JPEGs may contain MPF auxiliary images.
            im.load()  # Decode the full image, not the EXIF thumbnail.
            if im.format not in ('JPEG','MPO') or im.mode != 'RGB' or im.getexif().get(274, 1) != 1:
                raise ValueError('Expected an upright RGB JPEG')
            meta = metadata(im)
            container = im.format
            frame_count = getattr(im,'n_frames',1)
            frame = im.copy()
        record = dict(operator_sequence_label=label,
                      bytes=path.stat().st_size,sha256=digest(path),size=list(frame.size),
                      container=container,container_frames=frame_count,analyzed_frame=0,
                      full_decode_passed=True,metadata=meta,regions={})
        if path.name not in rois['files']:
            raise ValueError('Missing regions for ' + path.name)
        for name, spec in rois['files'][path.name].items():
            # Coordinates are marked on the 1920 x 1280 operator preview.
            box = tuple(round(v * frame.size[i % 2] / rois['reference_size'][i % 2])
                        for i, v in enumerate(spec))
            if not (0 <= box[0] < box[2] <= frame.width and 0 <= box[1] < box[3] <= frame.height):
                raise ValueError('ROI outside image')
            record['regions'][name] = region_stats(frame, box)
        images.append(frame)
        files.append(record)
    common = set(files[0]['regions'])
    if not common or any(set(f['regions']) != common for f in files):
        raise ValueError('Each photograph needs the same nonempty set of named regions')
    region = args.region if args.region is not None else min(common)
    if region not in common:
        p.error('--region must name a region present in all three images')
    stats = {}
    for name in sorted(common):
        r = [f['regions'][name] for f in files]
        v = [x['median_blue_red_chromaticity'] for x in r]
        stats[name] = dict(
            median_blue_red_chromaticity_A1_B_A2=v,
            all_three_median_B_above_R=all(x['median_RGB_0_255'][2] > x['median_RGB_0_255'][0] for x in r),
            B_minus_mean_A_chromaticity=v[1] - (v[0] + v[2]) / 2,
            A2_minus_A1_chromaticity=v[2] - v[0],
        )
    fixed = {k: all(f['metadata'].get(k) == files[0]['metadata'].get(k) for f in files)
             and files[0]['metadata'].get(k) is not None for k in FIXED}
    report = dict(schema='a6700-lab/jpeg-triplet-review/1',recorded_utc=datetime.now(timezone.utc).isoformat(),
        camera_access=False,source_images_modified=False,
        runtime=dict(python=platform.python_version(),pillow=pillow_version,numpy=np.__version__),
        script_sha256=digest(Path(__file__)),roi_sha256=digest(args.rois),files=files,
        fixed_metadata_equal=fixed,
        regions=stats,
        analysis='Manual same-object regions, all pixels included without hue selection; no exposure adjustment or image registration',
        PP_or_LUT_from_EXIF_verified=False,CP_state_verified=False,
        limitations=[
            'Composition changes; corresponding pixels are not registered and no whole-image equality is claimed',
            'Standard EXIF confirms manual white balance, not the exact temperature/tint or sensor gains',
            'No ICC conversion or linearization; statistics describe encoded JPEG RGB only',
            'Operator sequence supplies LUT labels; JPEG EXIF does not independently attest the selected LUT',
            'One A/B/A sequence and manually corresponding regions cannot exclude small or spatially limited effects',
        ])
    args.out_dir.mkdir(parents=True)
    font = ImageFont.load_default(size=22)
    small = ImageFont.load_default(size=18)
    canvas = Image.new('RGB', (1860, 900), (245, 245, 245));draw = ImageDraw.Draw(canvas)
    for index, (frame, record, label) in enumerate(zip(images,files,LABELS)):
        x = 20 + index * 620
        draw.text((x, 12), label, fill='black', font=font)
        thumb=frame.resize((600,400),Image.Resampling.LANCZOS)
        canvas.paste(thumb,(x,50))
        box=record['regions'][region]['box_native_pixels']
        draw.rectangle((x+box[0]*600/frame.width,50+box[1]*400/frame.height,
                        x+box[2]*600/frame.width,50+box[3]*400/frame.height),outline=(255,190,0),width=2)
        crop=frame.crop(box);crop.thumbnail((580,300),Image.Resampling.LANCZOS)
        canvas.paste(crop,(x,480))
        r=record['regions'][region]
        draw.text((x, 800), 'Region median RGB: ' + str(r['median_RGB_0_255']),fill='black',font=small)
        dominance = 'blue > red' if r['median_RGB_0_255'][2] > r['median_RGB_0_255'][0] else 'blue <= red'
        ratio = r['median_RGB_B_over_R']
        ratio_text = f'{ratio:.3f}' if ratio is not None else 'undefined'
        draw.text((x, 829), f'B/R: {ratio_text}, {dominance}',fill='black',font=small)
    draw.text((20,865),'Full JPEGs decoded; no brightness or color adjustment. ROI statistics are encoded JPEG RGB.',fill='black',font=small)
    canvas.save(args.out_dir/'comparison.png')
    (args.out_dir/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(files=[dict(label=f['operator_sequence_label'],size=f['size'],regions=f['regions']) for f in files],
                         fixed_metadata_equal=fixed,comparison=stats),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
