import re, sys
sys.path.insert(0, "scripts")
from common import SEC, load_config
sec = SEC(load_config())
sub = sec.json("https://data.sec.gov/submissions/CIK0001336528.json")
r = sub["filings"]["recent"]
for f, acc, doc, d in zip(r["form"], r["accessionNumber"], r["primaryDocument"], r["filingDate"]):
    if f == "13F-NT":
        base = f"https://www.sec.gov/Archives/edgar/data/1336528/{acc.replace('-', '')}/"
        idx = sec.get(base).text
        print("FILES", re.findall(r'href="([^"]+\.(?:xml|txt|htm))"', idx)[:10])
        for name in re.findall(r'href="(/Archives[^"]+\.xml)"', idx)[:3]:
            t = sec.get("https://www.sec.gov" + name).text
            print("DOC", name, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))[:1500])
        break
for name in ["Pershing Square Holdco", "Pershing Square Inc", "Pershing Square USA", "Pershing"]:
    t = sec.get("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=" + name.replace(" ", "+") + "&type=13F&owner=include&count=40&output=atom").text
    print("SEARCH", name, re.findall(r"<cik>(\d+)</cik>.*?<conformed-name>([^<]+)</conformed-name>", t, re.S)[:12] or re.findall(r"<conformed-name>([^<]+)</conformed-name>", t)[:12])
