# Publishing to Power BI Report Server: the report-format problem and how to fix it

> **Who this is for:** anyone on the team publishing `.pbix` reports to our on-premises **Power BI Report Server (PBIRS)**.
> **Tool:** [`tools/pbir_to_legacy.py`](../tools/pbir_to_legacy.py). It needs Python 3.9+ and nothing else (standard library only).
> Written 2026-10-21 after diagnosing the problem on `predicting_insurance_DSS4.pbix` (Aman Prediction). Converter calibrated against Desktop on 2026-10-22; drillthrough verified on the server.

---

## TL;DR

- **The problem:** reports saved with the regular Power BI Desktop use a new report format called **PBIR**, and **Power BI Report Server rejects PBIR files**.
- **Why it's easy to miss:** Desktop for Report Server can open these files but keeps saving them as PBIR, so they never become publishable.
- **The fix:**
  1. Switch off the two PBIR preview features in Desktop for Report Server (once per laptop).
  2. Convert each affected report with `tools/pbir_to_legacy.py`.
  3. Open the converted file in Desktop for Report Server, save it, and publish.
- **Rule going forward:** edit report-server reports **only** in *Power BI Desktop for Report Server*. Never open or save them in the regular Power BI Desktop.

---

## 1. Symptoms

You probably have this problem if you see any of these:

| Where | Message |
|---|---|
| Desktop → *File → Save as → Power BI Report Server* | `An error occurred while attempting to save the report to Power BI Report Server.` |
| Web portal → *Upload* (or the REST API) | `There was an error uploading your .pbix file. Verify that the file has not been corrupted and that the file extension matches the format of the file.` (error code `102`, HTTP 422) |
| Desktop for Report Server, opening a converted file made by an older converter version | `This file is corrupted or was created by an unrecognized version of Power BI Desktop.` (error code `MashupValidationError`) |

A brand-new report created in Desktop for Report Server publishes fine, but an existing report does not.

### Check a file in 10 seconds

A `.pbix` is a zip archive, so you can look inside it:

```bash
python -c "import zipfile,sys; n=zipfile.ZipFile(sys.argv[1]).namelist(); print('PBIR (server will reject)' if 'Report/definition/report.json' in n else 'classic (OK for Report Server)' if 'Report/Layout' in n else 'unknown')" "my_report.pbix"
```

---

## 2. Root cause

### Two report formats

| | **Classic format** | **PBIR (enhanced report format)** |
|---|---|---|
| Report pages stored as | one file: `Report/Layout` | a folder: `Report/definition/` (one JSON file per page, visual and bookmark) |
| Created by | Power BI Desktop for Report Server (new files) | the regular Power BI Desktop (default since early 2026) |
| **Accepted by Power BI Report Server** | ✅ yes | ❌ **no** |

### How a report ends up in the wrong format

1. Someone builds or edits the report in the **regular Power BI Desktop** (from the Microsoft Store or powerbi.com). That edition saves PBIR by default, and older files are upgraded to PBIR the first time they're saved.
2. The file is then opened in **Power BI Desktop for Report Server**. It can *read* PBIR, but it keeps the file in PBIR when saving, because its own PBIR preview features are switched **on**:
   - `Store PBIX reports using enhanced metadata format (PBIR)` (internal name `PBI_enhancedReportFormatPBIX`)
   - `Store reports using enhanced metadata format (PBIR)` (internal name `PBI_enhancedReportFormat`)
3. The Report Server only understands the classic format, so every upload fails.

### What we ruled out

We tested every part of the file separately against the server. Only the PBIR report pages are rejected.

| Tested | Result |
|---|---|
| Server reachable, user has *Content Manager* rights, max upload size 1,000 MB, custom visuals enabled | ✅ not the cause |
| Desktop and server on matching releases (Desktop **September 2026** ↔ server build 15.0.1122, Oct 2026) | needed, but **not** enough on its own |
| Data model (all tables, measures, Power Query) | ✅ accepted |
| Custom visuals, images, theme, settings, file version stamp `1.33` | ✅ accepted |
| TMDL-view and DAX-query-view tabs | ✅ not the cause |
| **Report pages in PBIR format** | ❌ **rejected** |

Microsoft provides no button to convert PBIR back to the classic format. Its documentation says you can't revert from the UI. That's why we wrote the converter.

---

## 3. The fix

### Step 1: set up each laptop once

1. Install **Power BI Desktop for Report Server**, the release that matches our server.
   - Get it from the server's web portal (`http://<server>/reports`) → **Download** (top right) → **Power BI Desktop**. That link always gives the matching version.
   - It installs next to the regular Desktop as a separate program (`C:\Program Files\Microsoft Power BI Desktop RS\`).
2. In Desktop for Report Server, go to **File → Options and settings → Options → Preview features**.
   - Untick **Store PBIX reports using enhanced metadata format (PBIR)**.
   - Untick **Store reports using enhanced metadata format (PBIR)**.
   - Click **OK**, then **close and reopen** Desktop.

### Step 2: convert existing reports

From the repo root:

```bash
# one file  ->  writes my_report_RS.pbix next to it
python tools/pbir_to_legacy.py "C:\path\my_report.pbix"

# one file with a chosen output name
python tools/pbir_to_legacy.py "C:\path\my_report.pbix" -o "C:\path\my_report_server.pbix"

# several files and/or whole folders at once
python tools/pbir_to_legacy.py "C:\path\a.pbix" "C:\path\b.pbix" "C:\Team\Reports"

# a whole folder into a separate output folder, and check each result opens in Desktop
python tools/pbir_to_legacy.py "C:\Team\Reports" --out-dir "C:\Team\Reports\converted" --verify
```

Example output:

```
SKIP     already_classic.pbix  (already in the classic format; no conversion needed)
FAILED   broken.pbix  (BadZipFile: File is not a zip file)
OK       team_report_A.pbix -> ...\converted\team_report_A_RS.pbix  (12 pages, 258 visuals, 32 bookmarks, 3 drillthrough)
           Desktop: opened
```

Each converted file is **self-checked**:
- pages, visuals, bookmarks and drillthrough pages are counted before and after, and any difference is printed as `check:`;
- any setting the converter hasn't been calibrated against is also printed as `check:`, so you know exactly which reports need a manual look.

`--verify` (Windows, needs Desktop for Report Server installed) opens each converted file in **Desktop for Report Server**, reports `opened` or `refused`, and closes Desktop again. A refused file counts as a failure.

How it behaves:
- **Input files are never modified.**
- **Files already in the classic format** are skipped.
- **Existing outputs** are not overwritten unless you add `--overwrite`.
- **One bad file** doesn't stop the batch. The script exits with code 1 if any file failed.
- **Use the newest save:** close the report in Power BI Desktop first, or convert the last saved version.

### Step 3: open, check, save, publish

For each converted file:
1. Open it in **Power BI Desktop for Report Server**.
2. Go through **every page**, using the checklist in section 5.
3. **Save** (Ctrl+S). With the preview features off, Desktop keeps the classic format and rebuilds the parts the converter left out.
4. Go to **File → Save as → Power BI Report Server**, enter `http://<server>/reports`, and choose the folder.
5. Open the report in the browser and check it again.

From now on, **the converted file is the master copy.** Archive the old PBIR file and don't edit it any more.

---

## 4. What the converter does

`tools/pbir_to_legacy.py` rewrites only the report layer and copies everything else unchanged.

| Part of the `.pbix` | What happens |
|---|---|
| `Report/definition/**` (pages, visuals, bookmarks, report settings) | **Converted** into one classic `Report/Layout` file: 1 page → 1 section, 1 visual → 1 visual container. Field references, sorting, visual filters, page filters, formatting, hidden visuals, groups, slicer sync groups, page interactions, tooltip pages, bookmarks and the theme are all carried across. **Drillthrough** pages are written the way Desktop stores them: a top-level `pods` entry per drillthrough page, linked to that page's drillthrough filters. |
| `DataModel` (tables, measures, relationships, Power Query) | Copied unchanged |
| `Report/CustomVisuals/**`, `Report/StaticResources/**` (custom visuals, images, themes) | Copied unchanged and registered in the new layout |
| `Version` | Kept as-is. **Do not downgrade it:** an older stamp makes Desktop look for the Power Query code in the wrong place, and it fails with `MashupValidationError`. |
| `SecurityBindings` | **Dropped.** It's encrypted and locked to the original file; if kept, Desktop refuses to open the converted file. Desktop recreates it on the next save. You may be asked to confirm data-source privacy levels or credentials once. |
| `TMDLScripts/**`, `DAXQueries/**` (TMDL-view and DAX-query-view tabs) | **Dropped.** The classic format can't store them. Copy anything you need from those tabs before converting. |
| `[Content_Types].xml` | Regenerated to match the new set of parts |
| Cached visual queries (`query` / `dataTransforms`) | Not written. Power BI rebuilds them the first time each visual renders. |

**Verified on `predicting_insurance_DSS4.pbix`** (12 pages, 258 visuals, 32 bookmarks, 3 drillthrough pages, 1 tooltip page, 6 custom visuals). A fresh conversion, with no manual fixes:
- opens in Desktop for Report Server (September 2026),
- uploads to the server,
- renders in the browser,
- drillthrough works on the server (right-click → *Drill through*).

### How the converter was calibrated

Microsoft doesn't publish the classic format's numeric codes. To get them right, the converted report was saved once in Desktop for Report Server, and Desktop's own `Report/Layout` was compared with the converter's output. Every difference was fixed in the converter (2026-10-22).

| Calibrated against Desktop | Not yet calibrated (the self-check warns if a report uses them) |
|---|---|
| Drillthrough, tooltip pages, edit-interactions, hidden pages, visual groups (scale mode), sorting, slicer sync, visual and page filters, custom visuals, images, theme, bookmarks | Report-level filters, bookmark groups, visual groups in scroll mode, *Fit to width* / *Actual size* page view, tooltip page bindings, unusual filter types |

If the self-check warns about one of these, check that feature in Desktop after converting. To calibrate it permanently, save the converted report in Desktop and compare it with the converter's output, as described above.

---

## 5. Checklist after converting a report

Quick checks on every converted report:

- [ ] The converter printed no `check:` lines, or you've checked each one in Desktop.
- [ ] `--verify` reported `Desktop: opened`.
- [ ] **Drillthrough pages:** right-click a data point → *Drill through* lists the page and filters it.
- [ ] **Bookmarks and navigation buttons:** each button goes to the right page or bookmark.
- [ ] **Hidden pages:** still hidden in the server viewer.
- [ ] **Custom visuals:** they render, and none show "Can't display this visual".
- [ ] **Report Server compatibility (not caused by the converter):**
  - The **new card visual** may render incompletely in Desktop for Report Server; it rendered correctly on our server. Prefer the classic **Card** for report-server reports.
  - **HTML Content** visuals rendered blank in one test browser (it blocked the server's custom-visual loader, `VisualResourceLoader.htm`). Check them in a normal browser.
- [ ] **Report Server performance:** on a small test server, the first open of a report takes 1–2 minutes while the data model loads. On a laptop with little free memory it can take much longer, so close other Power BI windows before testing.

If something is wrong, fix it in Desktop for Report Server and save. The fix stays, because the file is now classic.

---

## 6. Team rules

1. **Report-server reports are edited only in Power BI Desktop for Report Server**, the version downloaded from our server's portal.
2. **Never open a report-server `.pbix` in the regular Power BI Desktop.** One save there converts it back to PBIR.
3. Keep the **PBIR preview features switched off** in Desktop for Report Server. Check again after every Desktop update.
4. **Publish from Desktop** (*File → Save as → Power BI Report Server*) and check the result in the browser.
5. If a report gets converted to PBIR by accident, run `tools/pbir_to_legacy.py` on it again and repeat section 3, step 3.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Upload fails with *"error uploading your .pbix file"* | Report is in PBIR format | Convert it (section 3) |
| Converted file saves back as PBIR | PBIR preview features still on in Desktop for Report Server | Switch them off (section 3, step 1), restart Desktop, save again |
| Converted file won't open: *"corrupted or created by an unrecognized version"* | File made with an early converter version that kept `SecurityBindings` or changed `Version` | Re-run the current converter with `--overwrite` |
| *Drill through* missing from the right-click menu | File made with a converter from before 2026-10-22 (drillthrough not yet calibrated), or the source visual lacks the drillthrough fields | Re-run the current converter. The right-click only offers drillthrough on visuals that contain **all** the target page's drillthrough fields. |
| Report stuck on "Loading data" on the server | Server (or test laptop) short of memory | Close other Power BI windows. Remove old test copies from the server. Wait for the first load (1–2 min). |
| Save to server fails, and a brand-new blank report fails too | Desktop and server releases don't match, or the server has a problem | Install Desktop from the server portal's **Download** menu. If it still fails, ask an admin to check `C:\Program Files\Microsoft Power BI Report Server\PBIRS\LogFiles\RSPortal*` |
| *"Server is not reachable"* in the Save dialog | Wrong address | Use the portal address, e.g. `http://<server>/reports` (for a local test server: `http://localhost/reports`) |
| Visuals blank in Desktop but data is fine | Visual not supported by this Desktop version (e.g. the new card) | Replace with a supported visual, such as the classic Card |

---

## 8. References

- Microsoft Learn: [Power BI Desktop projects, report folder (PBIR)](https://learn.microsoft.com/power-bi/developer/projects/projects-report). Says there's no UI path back from PBIR to the legacy format.
- Power BI blog: [PBIR will become the default Power BI report format](https://powerbi.microsoft.com/blog/pbir-will-become-the-default-power-bi-report-format-get-ready-for-the-transition)
- Microsoft message center: [MC1215073, Power BI report metadata defaults to PBIR](https://mc.merill.net/message/MC1215073)
- Microsoft Q&A: [Failed to upload PBIX file to Power BI Report Server](https://learn.microsoft.com/answers/a/12536278). Says to use the matching Desktop for Report Server.
- Fabric Community: [Is it possible to save PBIR as PBIX and upload to PBIRS](https://community.fabric.microsoft.com/t5/Report-Server/is-it-possible-to-save-pbib-file-as-pbix-and-upload-to-PBIRS/m-p/4855730)
- Fabric Community: [New Card Visual not fully working on Report Server](https://community.fabric.microsoft.com/t5/Report-Server/New-Card-Visual-Not-Fully-Working-on-Report-Server/m-p/4883197)
