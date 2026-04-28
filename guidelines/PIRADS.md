# PI-RADS v2.1 — Prostate Imaging Reporting and Data System

PI-RADS v2.1 stratifies prostate MRI lesions by likelihood of clinically
significant prostate cancer (csPCa, defined as Gleason ≥ 3+4). It is
applied to multiparametric MRI (T2W + DWI + DCE) of the prostate in
biopsy-naïve patients or those on active surveillance.

## Categories

- **PI-RADS 1**: Very low — clinically significant cancer is highly
  unlikely.
- **PI-RADS 2**: Low — clinically significant cancer is unlikely.
- **PI-RADS 3**: Intermediate — clinically significant cancer is
  equivocal.
- **PI-RADS 4**: High — clinically significant cancer is likely.
- **PI-RADS 5**: Very high — clinically significant cancer is highly
  likely.

## Scoring by Zone

Each lesion's category is determined by the dominant sequence for its
zone, then modified by secondary sequences.

### Peripheral Zone (PZ) — DWI is dominant

DWI score (high b-value + ADC):
- **DWI 1**: Normal (no restriction).
- **DWI 2**: Linear, wedge-shaped low ADC; not focal.
- **DWI 3**: Focal, mildly/moderately hypointense on ADC; not markedly
  hyperintense on high-b DWI.
- **DWI 4**: Focal, markedly hypointense on ADC AND markedly
  hyperintense on high-b DWI; < 1.5 cm.
- **DWI 5**: Same features as DWI 4 but ≥ 1.5 cm OR definite
  extraprostatic extension/invasive behaviour.

DCE modifier for PZ lesions:
- DCE positive (focal early enhancement corresponding to a DWI 3 lesion)
  upgrades PI-RADS 3 → PI-RADS 4.
- DCE negative leaves the score unchanged.

### Transition Zone (TZ) — T2W is dominant

T2W score:
- **T2W 1**: Normal heterogeneous TZ signal.
- **T2W 2**: Circumscribed hypointense or heterogeneous encapsulated
  nodule (BPH).
- **T2W 3**: Heterogeneous signal with obscured margins.
- **T2W 4**: Lenticular or non-circumscribed, homogeneous, moderately
  hypointense; < 1.5 cm.
- **T2W 5**: Same features as T2W 4 but ≥ 1.5 cm OR definite
  extraprostatic extension/invasive behaviour.

DWI modifier for TZ lesions:
- DWI ≥ 4 upgrades T2W 3 → PI-RADS 4.

## Management Recommendations

PI-RADS does not prescribe management on imaging alone — the urologist
combines PI-RADS with PSA, PSA density (PSAD), age, family history, and
biopsy history. The recommendations below reflect current EAU /
NCCN guidance.

### PI-RADS 1
- **Action**: csPCa is highly unlikely on MRI. No targeted biopsy
  indicated based on imaging alone. Manage per clinical context (PSA
  trajectory, DRE).

### PI-RADS 2
- **Action**: csPCa is unlikely. No targeted biopsy indicated based on
  imaging alone. Continue clinical surveillance per urology pathway.

### PI-RADS 3
- **Action**: Equivocal. Recommend MR-targeted biopsy if PSA density
  ≥ 0.15 ng/mL/cc, family history of prostate cancer, or other elevated
  clinical risk. Otherwise consider repeat MRI in 6-12 months or
  systematic biopsy per urology pathway.
- This is the most clinically nuanced category — the imaging alone does
  not dictate management; clinical context drives the decision.

### PI-RADS 4
- **Action**: MR-targeted biopsy recommended (typically combined with
  systematic biopsy on the same session).

### PI-RADS 5
- **Action**: MR-targeted biopsy recommended (typically combined with
  systematic biopsy). Staging considerations (extraprostatic extension,
  seminal vesicle invasion, lymphadenopathy) should be reported and
  factor into surgical / radiation planning.

## Reporting Requirements

Per PI-RADS v2.1, every dominant lesion should be reported with:
1. **Location** (sector / zone — peripheral vs transition, side, level).
2. **Size** (longest diameter on the dominant sequence).
3. **PI-RADS category** (1-5).
4. **Extraprostatic extension** assessment for PI-RADS 4-5 lesions.
5. **Seminal vesicle invasion** if present.
6. **Lymphadenopathy** if present (≥ 8 mm short-axis suggests
   pathological nodes; report any suspicious node regardless of size).

Up to four highest-category lesions should be characterised; the
**index lesion** is the highest-category, then the largest if tied.

## Worked Examples

**Example 1**: 1.2 cm focal lesion in the right peripheral zone, mid
gland. Markedly hypointense on ADC, markedly hyperintense on high-b DWI.
DCE shows focal early enhancement.
→ DWI score 4 (focal, markedly hypointense, < 1.5 cm) = PI-RADS 4.
DCE positive does not upgrade further. Final: **PI-RADS 4**.
→ **MR-targeted biopsy recommended.**

**Example 2**: 2.0 cm peripheral zone lesion with markedly hypointense
ADC and markedly hyperintense high-b DWI.
→ DWI score 5 (≥ 1.5 cm) = **PI-RADS 5**.
→ **MR-targeted biopsy recommended; assess for extraprostatic extension
and seminal vesicle invasion.**

**Example 3**: 8 mm transition zone lesion with heterogeneous signal and
obscured margins on T2W. DWI score 3 (focal mildly hypointense on ADC).
→ T2W 3 + DWI 3 = **PI-RADS 3**.
→ **Equivocal. Recommend MR-targeted biopsy if PSA density ≥ 0.15
ng/mL/cc or other elevated clinical risk; otherwise repeat MRI in 6-12
months or proceed per urology pathway.**

**Example 4**: 14 mm peripheral zone lesion with definite extraprostatic
extension into the right neurovascular bundle.
→ Definite EPE = **PI-RADS 5** regardless of measurement.
→ **MR-targeted biopsy. Document EPE and laterality of NVB involvement
for surgical planning.**

## Output Requirements for Impressions

When applying PI-RADS:
1. State the lesion location, size, and PI-RADS category.
2. Name the specific management action — MR-targeted biopsy, MRI in
   6-12 months, or "no targeted biopsy indicated based on imaging
   alone." Do NOT use vague phrases like "consider biopsy" or "further
   evaluation."
3. For PI-RADS 3, name the PSA-density threshold and clinical-risk
   factors that would tip toward biopsy.
4. For PI-RADS 4-5, comment on extraprostatic extension and seminal
   vesicle invasion if assessable.
