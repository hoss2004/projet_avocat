"""Detect explicit instrument boundaries; ambiguous resets stay unidentified."""
import re
from app.utils.arabic import normalize_for_search

INSTRUMENT = re.compile(r"^(loi|decret(?:-loi)?|arrete|قانون|مرسوم|امر|قرار)\s+(?:(?:n[°oº]?|numero|عدد)\s*)?\d",re.I)
CODE = re.compile(r"^(?:code\s+(?:de|du|des)|مجلة\s|المجلة\s)",re.I)
FIRST = re.compile(r"(?:^(?:article|art\.|الفصل)\s*(?:1\b|premier\b|الاول\b|االول\b)|^1\s+الفصل)",re.M)

def boundary(lines,index):
    line=lines[index].strip()
    value=normalize_for_search(line)
    if not value or len(value)>350 or "http" in value: return None
    code_value=re.sub(r"^\d+\s*(?=مج|code)","",value)
    code_value=re.sub(r"^مج\s+لة", "مجلة",code_value)
    reversed_instrument=bool(re.search(r"(?:قانون عدد|امر مؤرخ في)\s*$",value) and re.search(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)",value))
    kind="instrument" if INSTRUMENT.match(value) or reversed_instrument else "code" if CODE.match(code_value) else None
    if not kind: return None
    if kind=="code" and (len(code_value)>100 or len(code_value.split())>12 or "فيما يتعلق" in code_value): return None
    # Require the beginning of a new numbered text nearby, not a mention inside an article.
    following="\n".join(normalize_for_search(x) for x in lines[index+1:index+45])[:2200]
    if not FIRST.search(following): return None
    if index>15 and lines[index-1].strip() and kind!="code": return None
    return {"title":line,"kind":kind,"confidence":"heading_detected","review_required":True}
