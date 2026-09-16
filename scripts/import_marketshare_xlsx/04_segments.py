#!/usr/bin/env python3
"""Assign each event one of the seven commercial segments.

    Grand Prix     professional circuits and pro-campaign offshore
    Premier Race   the marquee handicap and offshore events
    Race           everything else raced under handicap - club, regional, series
    One-Design     one-design class racing, keelboat and dinghy alike
    Classic        classic, vintage and metre-rule yachts
    Cruise         cruising rallies
    Premier Cruise superyacht regattas and blue-water rallies (ARC, Oyster)

Order matters: the first rule that fires wins. One-Design is driven off class
NAMES rather than words like "World Championship", so it cannot swallow the IRC
and ORC championships, which are handicap events.
"""
import csv, re, collections, pathlib

LEDGER = pathlib.Path(r"C:\Solent Marketshare\marketshare_project\marketshare\data\event_segments.csv")

CLASSES = (r"\bj ?/?(22|24|70|80|88|105|109|111|112|120|122)\b|\bj ?cup\b|"
           r"melges ?(14|15|17|19|20|24|32|40)?|etchell|dragon|\bstar\b|"
           r"lightning|snipe|\b49er\b|\b470\b|\b420\b|\b505\b|\bok\b|finn|"
           r"flying scot|thistle|shark|viper|vx ?one|martin 242|ensign|"
           r"atlantic class|atlantic nationals|yngling|daysailer|interlake|"
           r"\be ?scow\b|\bc ?scow\b|\bcal 25\b|\bic ?37\b|cape ?31|"
           r"beneteau 36|international 14|\bmoth\b|folkboat|knarr|sonar|"
           r"laser|\bilca\b|optimist|contender|fireball|\bsolo\b|squib|"
           r"\bxod\b|sunfish|highlander|rhodes 19|\bmc scow\b|hobie|nacra|"
           r"\b29er\b|tempest|soling|\bgp world\b|\bp40\b|surprise|"
           r"one.design|\bod\b|crescent|\bilya\b|kaszube|bacardi|"
           r"clubswan 28|swan one design|\bsb20\b|\brs ?\d")

SEG_RULES = [
    ("Premier Cruise", r"superyacht|super ?yacht|\bbucket\b|joysail|"
                       r"armani superyacht|the pursuit|oyster world rally|"
                       r"world rally|\barc\b|swan cup|swan heritage|contest cup"),
    ("Cruise",         r"cruising rally|rally\b|cruise in company"),
    ("Classic",        r"classic|clasica|clásica|vintage|herreshoff|opera house|"
                       r"richard mille|\b12 ?met(re|er)|\b12mr\b|\b12m world|"
                       r"\b8 ?met(re|er)|\b6 ?met(re|er)|johan anker|traditionals|"
                       r"vela clasica|\bgaff|camden classic"),
    ("Grand Prix",     r"52 super series|super series|\b44 ?cup\b|\brc ?44\b|"
                       r"america.s cup|\bthe ocean race\b|imoca|\bultim\b|figaro|"
                       r"class ?40|le defi azimut|odyss(e|é)e|vend(e|é)e|"
                       r"maxi yacht rolex|\bgl52\b|nations league|nations trophy|"
                       r"tour voile|foiling week|admiral.s cup|"
                       r"clubswan(?! 28)|swan porto|sardinia cup|\bmaxi\b|"
                       r"52 ?super|transat caf|normandy channel"),
    ("One-Design",     CLASSES),
    ("Premier Race",   r"fastnet|middle sea|sydney hobart|newport bermuda|"
                       r"transpac|copa del rey|voiles de saint|"
                       r"rolex big boat|giraglia|palma ?vela|"
                       r"round the island|round britain|caribbean 600|"
                       r"transatlantic|aegean 600|china sea|"
                       r"mackinac|cowes week|kiel week|spi ouest|"
                       r"silverrudder|gotl(a|u)nd runt|rund um|"
                       r"princesa sof(i|í)a|les voiles d|"
                       r"irc national|irc european| irc nationals|"
                       r"antigua sailing week|charleston race week|"
                       r"chester race week|hamilton island|swiftsure|"
                       r"rolex swan|tre golfi|newport regatta|nyyc annual"),
]
COMPILED = [(seg, re.compile(p, re.I)) for seg, p in SEG_RULES]

def segment_of(name):
    for seg, rx in COMPILED:
        if rx.search(name):
            return seg
    return "Race"          # everything else is handicap racing

def main():
    rows = list(csv.DictReader(open("staging_events_classified.csv", encoding="utf-8")))
    counts, ecount = collections.Counter(), collections.Counter()
    for r in rows:
        r["segment"] = segment_of(r["event_name"])
        counts[r["segment"]] += 1
        ecount[r["segment"]] += int(r["entries"])

    with open("staging_events_segmented.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    # the editable ledger, in the project's correction-file style
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["EventName", "Year", "Entries", "Segment", "Disposition",
                    "SourceFile"])
        for r in sorted(rows, key=lambda x: (x["segment"], -int(x["entries"]))):
            w.writerow([r["event_name"], r["year"], r["entries"], r["segment"],
                        r["disposition"], r["src_file"]])

    order = ["Grand Prix", "Premier Race", "Race", "One-Design", "Classic",
             "Cruise", "Premier Cruise"]
    print(f"{'segment':<16}{'events':>7}{'boats':>9}")
    for k in order:
        print(f"{k:<16}{counts[k]:>7}{ecount[k]:>9,}")
    print(f"{'TOTAL':<16}{sum(counts.values()):>7}{sum(ecount.values()):>9,}")
    print(f"\nledger -> {LEDGER}")
    for k in order:
        ex = [r["event_name"] for r in sorted(rows, key=lambda x: -int(x["entries"]))
              if r["segment"] == k][:6]
        if ex:
            print(f"\n{k}:")
            for e in ex:
                print("   ", e[:66])

if __name__ == "__main__":
    main()
