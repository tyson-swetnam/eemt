# Toolchain delta: legacy image versus the new workflow image (SDR-025)

Question: does moving from the legacy workflow image (Ubuntu 24.04 apt GRASS 8.3.2,
GDAL 3.8.4, PROJ 9.4.0) to the new conda-forge image (GRASS 8.5.0, GDAL 3.12.3,
PROJ 9.7.1) change any number, when both run the same explicit commands?

Answer, as measured on 2026-11-15:

- **GRASS: no change.** All 1,290 GRASS rasters compared are bit-identical between
  the two images, with equal NULL masks, geotransforms, CRS and nodata values. That is
  r.slope.aspect, r.sun mode 2 (five quantities on five days), r.sun mode 1 (five
  quantities at three times on two days), r.horizon (8 directions), 31 daily sums and
  the r.series monthly sum, on two real DEMs and eight synthetic ones.
- **GDAL: three kinds of change** in the operations the pipelines run outside GRASS:
  1. `gdalwarp` bilinear reprojection of the Float64 DEM `mcn_10m` changes in the last
     digits: at most 7.6e-10 m, or 3.1e-13 relative. Float32 DEMs are unchanged.
  2. `gdal_translate -projwin` returns one more row or column than GDAL 3.8.4 when the
     window edges fall inside pixels.
  3. Single-point coordinate transforms differ by a few nanometres at some points.

  Details are below. This report does not judge whether these changes are acceptable.
  That is the owner's sign-off on SDR-025 (finding F-A-036).

## Images and versions

| | legacy | new |
|---|---|---|
| image | `eemt:legacy-e7637f45c202` (sha256:e7637f45c202…, built 2026-02-01 from `docker/ubuntu/24.04`, last present in bd19386; exists only on the owner's build host) | `eemt-work/a7-eemt:3.0.0` (sha256:8d7efeffa6cc…, built from `docker/workflow/Dockerfile` at 08dd73e; same `conda-lock.yml` as `eemt:3.0.0`) |
| `g.version -rge`: GRASS | 8.3.2 (build 2024-03-31) | 8.5.0 (build 2026-05-16) |
| GRASS linked PROJ / GDAL / GEOS | 9.4.0 / 3.8.4 / 3.12.1 | 9.7.1 / 3.12.3 / 3.14.1 |
| `r.sun` | `/usr/lib/grass83/bin/r.sun`, linked to `libgomp.so.1` (ldd) | `/opt/eemt/env/lib/grass85/bin/r.sun`, linked to `libgomp.so.1` (ldd) |

The legacy image's default environment points `GISBASE` at `/usr/lib/grass84`, which
does not exist. The runs override it as recorded in the validation notes:
`GISBASE=/usr/lib/grass83`, `LD_LIBRARY_PATH=/usr/lib/grass83/lib`, PATH prefixed with
the GRASS `bin` and `scripts` directories, and `grass` started with `/usr/bin/python3`.

The legacy image contains **two** GDAL stacks, so the GDAL operations ran three times:

| variant | binaries | `gdalinfo --version` | `projinfo` | Python `osgeo` |
|---|---|---|---|---|
| legacy-apt | `/usr/bin` | GDAL 3.8.4 | PROJ 9.4.0 | 3.8.4 / PROJ 9.4.0 |
| legacy-conda | `/opt/conda/envs/eemt-gis/bin` (first on the image's PATH, so this is what the pipeline's Python and shell scripts called) | GDAL 3.11.5 | PROJ 9.7.1 | 3.11.5 / PROJ 9.7.1 |
| new | `/opt/eemt/env/bin` | GDAL 3.12.3 | PROJ 9.7.1 | 3.12.3 / PROJ 9.7.1 |

The legacy-apt stack is the one the legacy GRASS is linked to. As shipped, the
legacy-conda stack cannot resolve any EPSG code:
`gdalwarp -s_srs EPSG:26913 …` exits 1 with
`PROJ: proj_create_from_database: Open of /opt/conda/envs/eemt-gis/share/proj failed`,
although `proj.db` is present and world-readable. The legacy-conda runs therefore set
`PROJ_DATA` to that directory. This means `Tiff.warp` could not have succeeded in the
legacy image as shipped.

## Method

Scripts: `scripts/validation/toolchain_delta/` (`run.sh` drives everything; see its
header). Command used:

```bash
EEMT_LEGACY_IMAGE=eemt:legacy-e7637f45c202 \
EEMT_NEW_IMAGE=eemt-work/a7-eemt:3.0.0 \
EEMT_TD_FIXTURES=<s1-artifacts>/conventions/fixtures \
EEMT_TD_GORDONGULCH=<local copy>/gordongulch_dem_10m_3dep_cog.tif \
EEMT_TD_JOBS=12 \
scripts/validation/toolchain_delta/run.sh <outdir>
```

Every container ran with `--network none`, as the calling user, with the scripts
mounted read-only. The run took 6 minutes, as 72 container jobs, 12 at a time on a 255-core host. Exit
status 0.

**DEMs** (sha256 of the exact inputs is in the run's `inputs/SHA256SUMS`):

| DEM | size | type | CRS |
|---|---|---|---|
| `sol/examples/mcn_10m.tif` | 228 × 211, 10 m | Float64, nodata NaN | EPSG:26913 |
| Gordon Gulch, `gordongulch_dem_10m_3dep_cog.tif` (USGS 3DEP, from the owner's CyVerse folder; sha256 `2a0a5f0b…44b9`, checked by `run.sh`; not committed) | 434 × 296, 10 m | Float32, nodata −9999 | UTM 13N on GRS80 with a null `+towgs84`, no EPSG code |
| `plane_{N,E,S,W}`: 30° planes facing N/E/S/W | 200 × 200, 10 m | Float32 | EPSG:32612 |
| `wall_{N,E,S,W}`: 1000 m plain with a 150 m, 3-cell wall on one side | 200 × 200, 10 m | Float32 | EPSG:32612 |

The eight synthetic DEMs are the S1 convention fixtures. `make_fixtures.py` (a copy of
the S1 generator) run in the new image reproduces all eight byte for byte (sha256 equal).

**GRASS sequence** (`grass_suite.sh`, then `grass_rsum.sh`). It runs in a temporary
location created from the DEM, with the same commands and explicit parameters in both
images. Each result is written with `r.out.gdal createopt="COMPRESS=LZW" -c`, the
pipeline's export:

1. `r.in.gdal`; `g.region -sa raster=dem`; `r.slope.aspect elevation=dem slope=… aspect=…`.
2. r.sun mode 2: `r.sun elevation=dem aspect=… slope=… day=D step=0.25 linke_value=3.0 albedo_value=0.2 nprocs=4`
   with `glob_rad insol_time beam_rad diff_rad refl_rad`, for D = 1, 80, 172, 266, 355.
3. r.sun mode 1: the same with `time=9|12|15` (no `step`) and `glob_rad beam_rad diff_rad refl_rad incidout`, on days 80 and 355.
4. `r.horizon elevation=dem step=45` (8 directions).
5. Days 1–31 as in `sol/sol/rsun.sh` (`glob_rad=total_sun insol_time=hours_sun`). Then, as in
   `sol/sol/rsum.sh`, a new location from the first daily GeoTIFF, `r.in.gdal` of all 62
   files, `g.region -s`, and `r.series -n … method=sum` for both series. The only
   departure from `rsum.sh` is that its `g.remove -f "*"` is omitted, because GRASS 8
   rejects it.

That is 129 rasters per DEM.

**GDAL sequence** (`gdal_ops.sh`). The command lines come from the pipeline code:

| output | command | source |
|---|---|---|
| `warp_daymet` | `gdalwarp -s_srs EPSG:<epsg> -overwrite -t_srs "<DAYMET_proj>" -r bilinear -of GTiff` | `sol/sol/Tiff.py:157` (`Tiff.warp("DAYMET")`, the Daymet LCC string from `Tiff.py:12`) |
| `warp_3857_lzw` | `gdalwarp -overwrite -t_srs EPSG:3857 -r bilinear -of GTiff -dstnodata nan`, then `gdal_translate -co compress=LZW` | `sol/sol/parser.py:180,190` |
| `lzw` | `gdal_translate -co COMPRESS=LZW` of the DEM | the pipeline's LZW form |
| `projwin` | `gdal_translate -projwin ULX ULY LRX LRY -of GTiff` on the inner half of the DEM | `eemt/eemt/metget.sh:24`, `sol/sol/read_meta.py:334` |
| `points` | 41 × 41 grid of points over the DEM, transformed with `osgeo.osr` to the Daymet LCC and to EPSG:3857 | diagnostic |
| `diag_warp_daymet_et0` | `warp_daymet` with `-et 0` (exact transformer) | diagnostic |
| `diag_warp_daymet_near` | `warp_daymet` with `-r near` | diagnostic |

For Gordon Gulch, which has no EPSG code, `-s_srs` is omitted, so the CRS stored in the
file is used. (`Tiff.warp` itself would fail on this DEM, because it always passes
`-s_srs EPSG:<region>`.)

**Comparison** (`compare.py`). For every file at the same relative path in two result
trees, compare.py checks:

- dtype, size, geotransform, CRS (`osr.IsSame`), nodata;
- NULL masks (NaN or nodata) and whether they are equal.

Over pixels valid in both, it reports:

- the number of differing pixels;
- the maximum absolute difference;
- the maximum relative difference |a − b| / max(|a|, |b|);
- the maximum difference in ULPs of the stored type (1 = adjacent representable values).

"Bit-identical" requires equal metadata, equal NULL masks and identical bytes in every
valid pixel. Where sizes differ but the geotransform is equal, the common upper-left
window is compared.

`summarize.py` fails the run in four cases:

- a legacy-vs-new pair whose two trees report the same GRASS version (each tree records
  `g.version -rge`);
- a file present on one side only;
- a missing version record;
- an `r.sun` not linked to libgomp.

**Controls.** Each image ran the full GRASS suite twice. The two runs within each image
are bit-identical (1,290/1,290 for each image), so r.sun with `nprocs=4` is
deterministic here. A deliberately perturbed copy of one result tree was detected
correctly:

- one value moved by one float32 ULP: `n_diff` 1, `max_ulp` 1.0;
- one pixel set to NULL: NULL masks unequal;
- the geotransform origin moved by 1e-6 m: metadata unequal;
- one file deleted: reported as only on one side.

Setting the legacy tree's recorded version to 8.5.0 makes `summarize.py` exit 1. The
whole run was done twice, and the two summaries are identical.

## Results: GRASS (legacy 8.3.2 vs new 8.5.0)

| product | rasters (10 DEMs) | bit-identical | differing pixels | NULL masks equal |
|---|---|---|---|---|
| r.slope.aspect slope, aspect | 20 | 20 | 0 | yes |
| r.sun mode 2 `glob_rad` (days 1, 80, 172, 266, 355) | 50 | 50 | 0 | yes |
| r.sun mode 2 `beam_rad` / `diff_rad` / `refl_rad` | 150 | 150 | 0 | yes |
| r.sun mode 2 `insol_time` | 50 | 50 | 0 | yes |
| r.sun mode 1 `glob`/`beam`/`diff`/`refl` (days 80, 355 at 09, 12, 15 h) | 240 | 240 | 0 | yes |
| r.sun mode 1 `incidout` | 60 | 60 | 0 | yes |
| r.horizon, 8 directions | 80 | 80 | 0 | yes |
| daily `total_sun` / `hours_sun`, days 1–31 | 620 | 620 | 0 | yes |
| r.series monthly sum (January) `total_sun` / `hours_sun` | 20 | 20 | 0 | yes |
| **total** | **1,290** | **1,290** | **0** | **yes** |

The results are not trivial. On `mcn_10m`:

| output | range |
|---|---|
| day-172 `glob_rad` | 3,352.6 to 9,554.1 Wh m⁻² day⁻¹ |
| aspect | 0.01° to 359.98° |
| January monthly sum | 13,337 to 231,327 Wh m⁻² |

On both real DEMs, every raster has varying values. The 177 constant rasters are all
on the synthetic planes and walls, where a constant result is expected. The 4 all-NULL
rasters are `incidout` on planes that face away from the sun at that time, and both
images agree on those masks.

## Results: GDAL

Per DEM, 7 outputs. Below, "Float32 DEMs" means Gordon Gulch and the eight fixtures.

| output | legacy-apt (3.8.4 / 9.4.0) vs legacy-conda (3.11.5 / 9.7.1) | legacy-apt vs new (3.12.3 / 9.7.1) | legacy-conda vs new |
|---|---|---|---|
| `lzw` | identical, 10/10 | identical, 10/10 | identical, 10/10 |
| `warp_daymet` | identical, 10/10 | Float32 DEMs identical; **mcn_10m: 6,615 of 48,019 pixels differ**, max abs 7.6e-10 m, max rel 3.1e-13, 1,672 ULP (float64); NULL masks equal (6,206); size, geotransform, CRS equal | same as left (legacy-conda and legacy-apt are identical) |
| `warp_3857_lzw` | identical, 10/10 | Float32 DEMs identical; **mcn_10m: 6,144 of 48,060 differ**, max abs 1.6e-10 m, max rel 6.9e-14, 362 ULP; NULL masks equal (1,605) | same as left |
| `projwin` | **mcn_10m 114 × 106 vs 114 × 107; Gordon Gulch 217 × 148 vs 218 × 148**; fixtures 100 × 100 both; origin equal, overlapping pixels identical | same as left | identical, 10/10 |
| `diag_warp_daymet_et0` | mcn_10m: 51 pixels, max 1.2e-9 m; others identical | mcn_10m: 3,342 pixels, max 1.2e-9 m | mcn_10m: 3,293 pixels, max 1.1e-9 m |
| `diag_warp_daymet_near` | identical, 10/10 | identical, 10/10 | identical, 10/10 |
| `points` (1,681 per DEM and target) | Daymet: 2–4 points; EPSG:3857: 4–8 points; max 2.8e-9 m (≤ 97 ULP of the coordinate) | Daymet: 3–4 points; EPSG:3857: 213–355 points; max 2.8e-9 m (≤ 97 ULP) | Daymet: identical for mcn_10m and Gordon Gulch, 2 points for each fixture (max 1.4e-9 m, ≤ 12 ULP); EPSG:3857: 209–349 points, max 9.3e-10 m |

Values are from `summary.md` / `compare/*/<dem>.json` of the run. The full per-DEM table
is in the run output.

`projinfo` lists the same first-choice coordinate operation for both stacks:

- for EPSG:26913 → Daymet LCC, 12 candidates;
- for EPSG:26913 → EPSG:3857, 12 candidates;
- Gordon Gulch's BOUNDCRS gives one candidate.

### What the data show about the causes

- **Warps.** The warp outputs of GDAL 3.8.4 / PROJ 9.4.0 and of GDAL 3.11.5 /
  PROJ 9.7.1 are identical. Those of GDAL 3.12.3 / PROJ 9.7.1 differ. So the change in
  `warp_daymet` and `warp_3857_lzw` is not caused by the PROJ 9.4 → 9.7 upgrade. It
  appears between GDAL 3.11.5 and 3.12.3 with PROJ 9.7.1 on both sides.
  - The nearest-neighbour warp is identical in all pairs, so every output pixel still
    maps to the same source pixel.
  - The Daymet point transforms of legacy-conda and new are identical for `mcn_10m`.
  - The difference remains with the exact transformer (`-et 0`).

  So the change is in the sub-pixel arithmetic of the bilinear warp. The specific GDAL
  code change was not identified.
  
  The differences appear only for the Float64 DEM. The same operations on the nine
  Float32 DEMs give identical files, so at this size the differences do not survive
  rounding to float32.
- **`-projwin`.** The window edges fall inside pixels for `mcn_10m` (rows 52.75 to 158.25,
  in pixel units) and for Gordon Gulch (columns 108.5 to 325.5). They fall on pixel
  boundaries for the 200 × 200 fixtures.
  - GDAL 3.11.5 and 3.12.3 return every row or column the window touches: 107 rows and
    218 columns.
  - GDAL 3.8.4 returns one fewer, at the far edge. The origins are equal and the
    overlapping pixels are identical.
  - With boundary-aligned windows, all three versions agree.

  No reprojection is involved, so PROJ is not the cause. The change lies between GDAL
  3.8.4 and 3.11.5. In the legacy image the pipeline's shell scripts called the conda
  GDAL 3.11.5 (first on PATH), which already behaves like the new image.
- **Point transforms.** Two builds of the same PROJ 9.7.1 (legacy-conda and new) differ
  at 209–349 of 1,681 points for EPSG:3857, by up to 9.3e-10 m. So differences of this
  size occur without a PROJ version change. Their cause (compiler, libm, build options)
  was not determined.

## Not covered

- `r.mapcalc` expressions (`eemt/eemt/reemt.sh`, `twi.sh`). reemt.sh cannot run on
  GRASS 8, and twi.sh needs SAGA, which neither image has (F-A-020).
- r.sun with horizon or Linke/albedo raster inputs, other `step` values, other `nprocs`,
  and `linke_value = 1.9` (SDR-069). The identity was shown only for the parameters
  above.
- A full 365-day run, the Makeflow drivers, Daymet download, and Python-side NumPy
  arithmetic.
- The legacy image's GRASS was run with the corrected environment above. As shipped,
  its default `GISBASE` does not exist.

## Re-running

Set `EEMT_LEGACY_IMAGE` to the legacy image. It exists only on the build host above
and cannot be rebuilt, so the scripts do not name it. Set `EEMT_NEW_IMAGE`; the default
is `eemt:<docker/VERSION>`. Then run `run.sh OUTDIR`. Without `EEMT_TD_FIXTURES` the
fixtures are generated in the new image. Without `EEMT_TD_GORDONGULCH` that DEM is
skipped. The run writes `OUTDIR/summary.md` and `summary.json`.
