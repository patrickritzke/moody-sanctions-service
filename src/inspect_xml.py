"""
Schema discovery tool for Moody's RDC feed files.

Usage:
    python src/inspect_xml.py                        # inspect entities XML (first 5 records)
    python src/inspect_xml.py --file relationships   # inspect relationships
    python src/inspect_xml.py --file dictionary      # inspect dictionary
    python src/inspect_xml.py --xsd                  # print XSD summary instead
    python src/inspect_xml.py --n 10                 # show 10 records

Run this BEFORE load_entities.py to confirm element names match what the
loader expects. If they differ, update ENTITY_TAG and extract_entity() there.
"""
import argparse
import os
from pathlib import Path
from collections import defaultdict, Counter

from dotenv import load_dotenv
import yaml
from lxml import etree

load_dotenv()

with open("config.yaml") as f:
    config = yaml.safe_load(f)

DATA_DIR = Path(os.environ["RDC_DATA_DIR"])


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def summarise_xsd(xsd_path: Path) -> None:
    print(f"\n=== XSD Summary: {xsd_path.name} ===\n")
    tree = etree.parse(str(xsd_path))
    root = tree.getroot()
    ns = {"xs": "http://www.w3.org/2001/XMLSchema"}

    elements = root.findall(".//xs:element", ns)
    print(f"Total <xs:element> declarations: {len(elements)}\n")

    for el in elements:
        name = el.get("name", "—")
        typ = el.get("type", "")
        minOcc = el.get("minOccurs", "1")
        maxOcc = el.get("maxOccurs", "1")
        attrs = []
        for attr in el.findall(".//xs:attribute", ns):
            attrs.append(attr.get("name", "?"))
        attr_str = f"  attrs=[{', '.join(attrs)}]" if attrs else ""
        occ = f"[{minOcc}..{maxOcc}]" if (minOcc != "1" or maxOcc != "1") else ""
        print(f"  {name} {typ} {occ}{attr_str}")

    print("\nTop-level complex types:")
    for ct in root.findall("xs:complexType", ns):
        print(f"  {ct.get('name', '—')}")


def inspect_xml(xml_path: Path, max_records: int = 5) -> None:
    print(f"\n=== XML Inspection: {xml_path.name} (first {max_records} records) ===\n")

    tag_counts: Counter = Counter()
    attr_map: dict = defaultdict(set)
    text_samples: dict = defaultdict(list)

    record_count = 0
    root_tag = None
    repeating_tag = None

    context = etree.iterparse(str(xml_path), events=("start", "end"), recover=True)

    for event, elem in context:
        tag = strip_ns(elem.tag)

        if event == "start" and root_tag is None:
            root_tag = tag
            print(f"Root element : <{tag}>")
            for k, v in elem.attrib.items():
                print(f"  Root attr  : {strip_ns(k)}={v!r}")
            continue

        if event == "end":
            if repeating_tag is None and elem.getparent() is not None:
                parent_tag = strip_ns(elem.getparent().tag)
                if parent_tag == root_tag:
                    repeating_tag = tag
                    print(f"Repeating element (detected): <{tag}>\n")

            if repeating_tag and tag == repeating_tag:
                record_count += 1
                if record_count > max_records:
                    break

                print(f"--- Record {record_count} ---")
                print(f"  Attributes: {dict(elem.attrib)}")

                for child in elem.iter():
                    ctag = strip_ns(child.tag)
                    tag_counts[ctag] += 1
                    for k, v in child.attrib.items():
                        attr_map[ctag].add(strip_ns(k))
                    if child.text and child.text.strip():
                        val = child.text.strip()
                        if len(text_samples[ctag]) < 3:
                            text_samples[ctag].append(val)

                def _dump(el, indent=2):
                    tag_ = strip_ns(el.tag)
                    txt = (el.text or "").strip()
                    attr_str = " ".join(f'{strip_ns(k)}="{v}"' for k, v in el.attrib.items())
                    line = " " * indent + f"<{tag_}"
                    if attr_str:
                        line += f" {attr_str}"
                    if txt:
                        line += f">{txt}</{tag_}>"
                    elif len(el):
                        line += ">"
                        print(line)
                        for c in el:
                            _dump(c, indent + 2)
                        return
                    else:
                        line += "/>"
                    print(line)

                _dump(elem)
                print()

                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]

    print(f"\nTotal records sampled: {record_count}")
    print(f"\nAll child tags seen (across {record_count} records):")
    for tag_, count in sorted(tag_counts.items()):
        attrs = sorted(attr_map.get(tag_, []))
        samples = text_samples.get(tag_, [])
        attr_str = f"  attrs=[{', '.join(attrs)}]" if attrs else ""
        sample_str = f"  e.g. {samples[:2]}" if samples else ""
        print(f"  {tag_:<30} count={count:>4}{attr_str}{sample_str}")


def main():
    parser = argparse.ArgumentParser(description="Inspect RDC feed XML or XSD structure")
    parser.add_argument("--file", default="entities",
                        choices=["entities", "relationships", "sources", "dictionary"],
                        help="Which feed file to inspect (default: entities)")
    parser.add_argument("--xsd", action="store_true",
                        help="Inspect XSD schema instead of XML data")
    parser.add_argument("--n", type=int, default=5,
                        help="Number of records to sample (default: 5)")
    args = parser.parse_args()

    if args.xsd:
        xsd_path = DATA_DIR / config["files"]["entities_xsd"]
        summarise_xsd(xsd_path)
    else:
        xml_key = args.file
        xml_path = DATA_DIR / config["files"][xml_key]
        inspect_xml(xml_path, max_records=args.n)


if __name__ == "__main__":
    main()
