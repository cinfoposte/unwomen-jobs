# UN Women Job Vacancies – RSS Feed

Automated scraper that extracts international professional-level job vacancies from the [UN Women Oracle Cloud careers site](https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs) and publishes them as an RSS 2.0 feed via GitHub Pages.

**RSS feed URL:** <https://cinfoposte.github.io/unwomen-jobs/unwomen_jobs.xml>

## What it does

- Scrapes the UN Women Oracle Cloud Candidate Experience portal using headless Chrome (Selenium).
- Filters jobs to include only **P-1 through P-5**, **D-1/D-2**, **internships**, and **fellowships**.
- Excludes consultancies, G-level, National Officer (NO-A/B/C/D), SB, and LSC grades.
- Outputs a valid RSS 2.0 XML feed (`unwomen_jobs.xml`) with feed accumulation (new jobs are appended, existing ones preserved).
- Runs automatically via GitHub Actions every **Thursday and Sunday at 06:00 UTC**.

## Local run

```bash
# Install dependencies
pip install -r requirements.txt

# Make sure Chrome / Chromium is installed locally
# Run the scraper
python scraper.py
```

The output file `unwomen_jobs.xml` will be created/updated in the repo root.

## GitHub Pages activation

1. Go to **Settings → Pages** in this repository.
2. Under **Source**, select **Deploy from a branch**.
3. Choose branch **main** and folder **/ (root)**.
4. Click **Save**.
5. The feed will be available at: `https://cinfoposte.github.io/unwomen-jobs/unwomen_jobs.xml`

## cinfoPoste import mapping

| Portal-Feld | Dropdown-Auswahl |
|-------------|-----------------|
| TITLE       | → Title         |
| LINK        | → Link          |
| DESCRIPTION | → Description   |
| PUBDATE     | → Date          |
| ITEM        | → Start item    |
| GUID        | → Unique ID     |
