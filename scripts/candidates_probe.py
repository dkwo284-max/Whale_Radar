import re, sys
sys.path.insert(0, "scripts")
from common import SEC, load_config
sec = SEC(load_config())
for name in ["Greenlight Capital", "DME Capital", "DME Advisors", "Pershing Square", "Scion Asset", "Scion Capital"]:
    url = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=" + name.replace(" ", "+") + "&type=13F-HR&owner=include&count=20&output=atom"
    try:
        t = sec.get(url).text
    except Exception as e:
        print("ERR", name, e); continue
    pairs = re.findall(r"<cik>(\d+)</cik>.*?<conformed-name>([^<]+)</conformed-name>", t, re.S) or re.findall(r"<conformed-name>([^<]+)</conformed-name>\s*<cik>(\d+)</cik>", t)
    print("SEARCH", name, pairs[:8], len(t))
    if not pairs:
        m = re.search(r"<title>(.*?)</title>", t); print("  title", m.group(1) if m else t[:200])
for cik in [1079114, 1336528, 1649339, 1489933]:
    try:
        sub = sec.json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
        r = sub["filings"]["recent"]
        rows = [(f, d, rd) for f, d, rd in zip(r["form"], r["filingDate"], r["reportDate"]) if f.startswith("13F")][:6]
        print("CIK", cik, sub.get("name"), rows, "formerNames", sub.get("formerNames"))
    except Exception as e:
        print("ERR cik", cik, e)
