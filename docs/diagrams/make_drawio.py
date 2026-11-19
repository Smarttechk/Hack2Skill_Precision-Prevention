"""
Builds the project's architecture diagrams as draw.io files.

    python docs/diagrams/make_drawio.py

Writes, next to this script:
    all_diagrams.drawio            three pages in one file
    1_end_to_end_architecture.drawio
    2_synthetic_data_pipeline.drawio
    3_seven_agents.drawio
    paste_1/2/3_*.xml               the same pages as plain mxGraphModel, for Extras > Edit Diagram
Open any of them at https://app.diagrams.net (File > Open from > Device) or in draw.io desktop.
Arrows are real connectors and boxes sit inside real containers, so things stay attached when moved.
"""
import os
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- styles
FONT = "fontFamily=Helvetica;"
BOX = ("rounded=1;whiteSpace=wrap;html=1;arcSize=10;align=left;verticalAlign=top;spacingLeft=10;spacingTop=6;"
       "fillColor=#FFFFFF;strokeColor=#9E9E9E;fontColor=#1F1F1F;fontSize=13;" + FONT)
ACCENT = ("rounded=1;whiteSpace=wrap;html=1;arcSize=10;align=left;verticalAlign=top;spacingLeft=10;spacingTop=6;"
          "fillColor=#DAE8FC;strokeColor=#1F6FD1;strokeWidth=2;fontColor=#1F1F1F;fontSize=13;" + FONT)
SOURCE = ("rounded=1;whiteSpace=wrap;html=1;arcSize=10;align=left;verticalAlign=top;spacingLeft=10;spacingTop=6;"
          "fillColor=#FFFFFF;strokeColor=#9E9E9E;dashed=1;dashPattern=4 3;fontColor=#1F1F1F;fontSize=13;" + FONT)
STEP = ("rounded=1;whiteSpace=wrap;html=1;arcSize=6;align=left;verticalAlign=middle;spacingLeft=14;"
        "fillColor=#FFFFFF;strokeColor=#BDBDBD;fontColor=#1F1F1F;fontSize=12;" + FONT)
BAND = ("rounded=1;whiteSpace=wrap;html=1;arcSize=4;container=1;collapsible=0;recursiveResize=0;"
        "align=left;verticalAlign=top;spacingLeft=14;spacingTop=4;fontStyle=1;"
        "fillColor=#F3F3F1;strokeColor=#BDBDBD;fontColor=#1F1F1F;fontSize=13;" + FONT)
TITLE = "text;html=1;align=left;verticalAlign=middle;fontSize=16;fontStyle=1;fontColor=#1F1F1F;" + FONT
NOTE = "text;html=1;align=left;verticalAlign=middle;fontSize=12;fontColor=#5F5F5F;" + FONT
EDGE = ("edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;endSize=6;"
        "strokeColor=#9E9E9E;strokeWidth=1.25;fontColor=#5F5F5F;fontSize=11;" + FONT)


def label(name, *lines):
    body = "".join(f"<br><font style=\"font-size:11.5px\" color=\"#5F5F5F\">{l}</font>" for l in lines)
    return f"<b>{name}</b>{body}"


class Page:
    def __init__(self, name, page_id, width, height):
        self.name, self.page_id = name, page_id
        self.model = ET.Element("mxGraphModel", dx="1000", dy="800", grid="1", gridSize="8", guides="1",
                                tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1",
                                pageWidth=str(width), pageHeight=str(height), math="0", shadow="0")
        self.root = ET.SubElement(self.model, "root")
        ET.SubElement(self.root, "mxCell", id="0")
        ET.SubElement(self.root, "mxCell", id="1", parent="0")
        self.ids = set()

    def vertex(self, cid, value, style, x, y, w, h, parent="1"):
        assert cid not in self.ids, cid
        self.ids.add(cid)
        c = ET.SubElement(self.root, "mxCell", id=cid, value=value, style=style, vertex="1", parent=parent)
        ET.SubElement(c, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), **{"as": "geometry"})
        return cid

    def edge(self, cid, src, dst, value="", extra="", points=()):
        assert cid not in self.ids, cid
        self.ids.add(cid)
        c = ET.SubElement(self.root, "mxCell", id=cid, value=value, style=EDGE + extra, edge="1",
                          parent="1", source=src, target=dst)
        g = ET.SubElement(c, "mxGeometry", relative="1", **{"as": "geometry"})
        if points:
            arr = ET.SubElement(g, "Array", **{"as": "points"})
            for px, py in points:
                ET.SubElement(arr, "mxPoint", x=str(px), y=str(py))
        return cid

    def diagram(self):
        d = ET.Element("diagram", id=self.page_id, name=self.name)
        d.append(self.model)
        return d


# ---------------------------------------------------------------- 1. end-to-end architecture
def end_to_end():
    p = Page("1 End-to-end architecture", "e2e", 780, 900)
    p.vertex("title", "From raw data to a completed check-up, with the doctor deciding", TITLE, 24, 12, 700, 32)
    bands = [("b1", "1. Sources", 56, 120), ("b2", "2. Data", 200, 120),
             ("b3", "3. Decisions, run daily by the orchestrator", 344, 204),
             ("b4", "4. Engagement and safety", 572, 120), ("b5", "5. People and systems", 716, 120)]
    for bid, name, y, h in bands:
        p.vertex(bid, name, BAND, 24, y, 660, h)
    four = [16, 176, 336, 496]   # x inside the band
    three = [16, 230, 444]
    p.vertex("s1", label("Synthea", "synthetic patients,", "6 age-sex groups"), BOX, four[0], 32, 148, 72, "b1")
    p.vertex("s2", label("Real public data", "NHANES, WHO, World", "Bank, OSM, calendar"), BOX, four[1], 32, 148, 72, "b1")
    p.vertex("s3", label("Reference files", "guidelines,", "templates, replies"), BOX, four[2], 32, 148, 72, "b1")
    p.vertex("s4", label("Malaffi (pilot)", "FHIR records,", "after approval"), BOX, four[3], 32, 148, 72, "b1")
    p.vertex("d1", label("Build and adjust", "get_all_data.py and", "uae_adjust.py"), BOX, three[0], 32, 200, 72, "b2")
    p.vertex("d2", label("Patient table", "one row per person,", "consent applied"), ACCENT, three[1], 32, 200, 72, "b2")
    p.vertex("d3", label("Truth file (locked)", "hidden cases, used only", "by the simulation"), BOX, three[2], 32, 200, 72, "b2")
    p.vertex("c1", label("Care-gap rules", "24 guideline rules,", "warning signs"), BOX, three[0], 32, 200, 72, "b3")
    p.vertex("c2", label("Risk score", "published FINDRISC,", "reasons, range, NHANES-checked"), BOX, three[1], 32, 200, 72, "b3")
    p.vertex("c3", label("Pathway cards", "outreach invite or", "clinician decides"), BOX, three[2], 32, 200, 72, "b3")
    p.vertex("c4", label("No-show rule", "adds support only,", "never lowers priority"), BOX, three[0], 116, 200, 72, "b3")
    p.vertex("c5", label("Follow-up learner", "picks reminder, channel", "or health worker"), BOX, three[1], 116, 200, 72, "b3")
    p.vertex("c6", label("Evaluation", "results, fairness,", "worst case, reports"), BOX, three[2], 116, 200, 72, "b3")
    p.vertex("e1", label("Message composer", "approved templates,", "timing, safety check"), BOX, three[0], 32, 200, 72, "b4")
    p.vertex("e2", label("Chat", "fixed rules and", "approved replies"), BOX, three[1], 32, 200, 72, "b4")
    p.vertex("e3", label("Consent and audit", "per-purpose consent,", "every action logged"), BOX, three[2], 32, 200, 72, "b4")
    p.vertex("o1", label("Dashboard", "doctors agree or", "disagree; managers"), BOX, three[0], 32, 200, 72, "b5")
    p.vertex("o2", label("Families", "WhatsApp, SMS, voice,", "health worker visits"), BOX, three[1], 32, 200, 72, "b5")
    p.vertex("o3", label("Clinic systems", "FHIR Tasks to Malaffi", "and clinic work lists"), BOX, three[2], 32, 200, 72, "b5")
    for i, (a, b) in enumerate([("b1", "b2"), ("b2", "b3"), ("b3", "b4"), ("b4", "b5")]):
        p.edge(f"f{i}", a, b, extra="exitX=0.5;exitY=1;entryX=0.5;entryY=0;")
    p.edge("loop", "b5", "b3", "doctor decisions, replies, attendance",
           extra="exitX=1;exitY=0.5;entryX=1;entryY=0.5;labelPosition=right;align=left;verticalLabelPosition=middle;",
           points=[(712, 776), (712, 446)])
    return p


# ---------------------------------------------------------------- 2. synthetic data pipeline
def pipeline():
    p = Page("2 Synthetic data pipeline", "pipeline", 780, 820)
    p.vertex("title", "Synthetic patients are built in four stages, then checked by 25 tests", TITLE, 24, 10, 720, 30)
    p.vertex("sub", "Left: what our scripts do. Right, dashed: the real data each stage uses.", NOTE, 24, 38, 720, 20)
    p.vertex("g", label("1. Generate with Synthea", "6 runs, one per age and sex group: 5,000 living people, about 10 minutes"), BOX, 24, 72, 480, 56)
    p.vertex("c", label("2. Combine", "stack the runs into data/raw; drop 5 unused files (billing, supplies, imaging)"), BOX, 24, 152, 480, 56)
    p.vertex("m", label("3. Map codes", "match 30 project fields to codes, such as HbA1c, BMI, well-child visit"), BOX, 24, 232, 480, 56)
    p.vertex("adj", "4. Adjust to the UAE (uae_adjust.py)", BAND, 24, 312, 480, 276)
    subs = [("a1", "a. Homes in 401 real places, distance to the nearest of 360 health facilities"),
            ("a2", "b. People: nationality, language, insurance, shifts, guardians"),
            ("a3", "c. Missed well-child visits, tuned to a 92% second measles dose"),
            ("a4", "d. Hidden diabetes and hypertension and smoking, matched to WHO totals"),
            ("a5", "e. Remove records dated in the future")]
    for i, (sid, text) in enumerate(subs):
        p.vertex(sid, text, STEP, 16, 32 + 48 * i, 448, 40, "adj")
    p.vertex("o1", label("Adjusted records", "data/processed/"), BOX, 24, 614, 152, 56)
    p.vertex("o2", label("Truth file", "simulation only"), BOX, 188, 614, 152, 56)
    p.vertex("o3", label("Summary", "adjust_summary.json"), BOX, 352, 614, 152, 56)
    p.vertex("k", label("Checked", "25 data tests and DATA_REPORT.md: no problems found"), ACCENT, 24, 694, 480, 56)
    p.vertex("wb", label("World Bank", "UAE age and sex, 2024"), SOURCE, 536, 72, 200, 56)
    p.vertex("osm", label("OpenStreetMap", "401 places, 360 health facilities"), SOURCE, 536, 336, 200, 56)
    p.vertex("who", label("WHO", "measles second dose,", "diabetes, hypertension,", "smoking targets"), SOURCE, 536, 436, 200, 96)
    for i, (a, b) in enumerate([("g", "c"), ("c", "m"), ("m", "adj")]):
        p.edge(f"s{i}", a, b, extra="exitX=0.5;exitY=1;entryX=0.5;entryY=0;")
    for i, o in enumerate(["o1", "o2", "o3"]):
        p.edge(f"out{i}", "adj", o, extra=f"exitX={(76 + i * 164) / 480:.3f};exitY=1;entryX=0.5;entryY=0;")
        p.edge(f"chk{i}", o, "k", extra=f"exitX=0.5;exitY=1;entryX={(76 + i * 164) / 480:.3f};entryY=0;")
    p.edge("in1", "wb", "g", extra="exitX=0;exitY=0.5;entryX=1;entryY=0.5;")
    p.edge("in2", "osm", "a1", extra="exitX=0;exitY=0.5;entryX=1;entryY=0.5;")
    p.edge("in3", "who", "a3", extra="exitX=0;exitY=0.3;entryX=1;entryY=0.5;")
    p.edge("in4", "who", "a4", extra="exitX=0;exitY=0.8;entryX=1;entryY=0.5;")
    return p


# ---------------------------------------------------------------- 3. seven agents
def seven_agents():
    p = Page("3 Seven agents", "agents", 780, 600)
    p.vertex("title", "Seven agents share one patient table; doctors stay in the loop", TITLE, 24, 12, 720, 32)
    xa, xb, xc = 64, 296, 528
    p.vertex("in1", label("Synthetic patients", "5,000, adjusted to the UAE"), BOX, xa, 60, 208, 64)
    p.vertex("in2", label("Real data", "NHANES, WHO, clinics, calendar"), BOX, xb, 60, 208, 64)
    p.vertex("in3", label("Guidelines and templates", "24 rules, approved messages"), BOX, xc, 60, 208, 64)
    p.vertex("prep", "Data preparation: one row per patient, consent applied before any use",
             BOX.replace("align=left", "align=center").replace("verticalAlign=top", "verticalAlign=middle"), 64, 152, 672, 48)
    p.vertex("orch", "Orchestrator: runs the agents in order, every simulated day", BAND, 64, 228, 672, 220)
    ax = [16, 180, 344, 508]
    agents = [("a1", "Care-gap agent", "overdue checks,", "warning signs"), ("a2", "Risk agent", "score with range,", "top 3 reasons"),
              ("a3", "Priority step", "urgency, deadline,", "pathway card"), ("a4", "Outreach agent", "channel, template,", "contact time"),
              ("a5", "Chat assistant", "replies, booking,", "consent, emergency"), ("a6", "Follow-up agent", "reminders; learns", "what works"),
              ("a7", "Evaluation agent", "results, fairness,", "daily report")]
    for i, (aid, n, l1, l2) in enumerate(agents):
        p.vertex(aid, label(n, l1, l2), BOX, ax[i % 4], 44 if i < 4 else 132, 148, 72, "orch")
    p.vertex("tbl", label("Patient table", "read and written", "by every agent"), ACCENT, ax[3], 132, 148, 72, "orch")
    for i in range(6):
        a, b = agents[i][0], agents[i + 1][0]
        if i == 3:   # row break: outreach -> chat
            p.edge(f"ag{i}", a, b, extra="exitX=0.5;exitY=1;entryX=0.5;entryY=0;")
        else:
            p.edge(f"ag{i}", a, b, extra="exitX=1;exitY=0.5;entryX=0;entryY=0.5;")
    p.vertex("o1", label("React app, 10 pages", "doctors, outreach, managers"), BOX, xa, 476, 208, 64)
    p.vertex("o2", label("Messages to families", "WhatsApp, SMS, voice, visits"), BOX, xb, 476, 208, 64)
    p.vertex("o3", label("FHIR Tasks", "to Malaffi and clinic systems"), BOX, xc, 476, 208, 64)
    for i, s in enumerate(["in1", "in2", "in3"]):
        p.edge(f"i{i}", s, "prep", extra="exitX=0.5;exitY=1;entryX=" + ["0.155", "0.5", "0.845"][i] + ";entryY=0;")
    p.edge("p0", "prep", "orch", extra="exitX=0.5;exitY=1;entryX=0.5;entryY=0;")
    for i, o in enumerate(["o1", "o2", "o3"]):
        p.edge(f"o{i}e", "orch", o, extra="exitX=" + ["0.155", "0.5", "0.845"][i] + ";exitY=1;entryX=0.5;entryY=0;")
    p.edge("loop", "o1", "orch", extra="exitX=0;exitY=0.5;entryX=0;entryY=0.5;",
           points=[(40, 508), (40, 338)])
    p.vertex("loop-label", "Agree or Disagree", NOTE.replace("align=left", "align=center") + "rotation=-90;",
             -36, 413, 120, 20)
    return p


def write(pages, path):
    mx = ET.Element("mxfile", host="app.diagrams.net", agent="make_drawio.py", version="24.7.17")
    for p in pages:
        mx.append(p.diagram())
    ET.indent(mx)
    ET.ElementTree(mx).write(path, encoding="utf-8", xml_declaration=False)
    return path


if __name__ == "__main__":
    pages = [end_to_end(), pipeline(), seven_agents()]
    print(write(pages, os.path.join(HERE, "all_diagrams.drawio")))
    for p, name in zip(pages, ["1_end_to_end_architecture", "2_synthetic_data_pipeline", "3_seven_agents"]):
        print(write([p], os.path.join(HERE, f"{name}.drawio")))
        paste = os.path.join(HERE, f"paste_{name}.xml")           # paste into draw.io: Extras > Edit Diagram
        with open(paste, "w", encoding="utf-8") as fh:
            fh.write(ET.tostring(p.model, encoding="unicode"))
        print(paste)
