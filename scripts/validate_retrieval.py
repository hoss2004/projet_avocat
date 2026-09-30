from pathlib import Path
from dotenv import dotenv_values
import httpx,json
root=Path(__file__).resolve().parents[1]
s=dotenv_values(root/".env")
before=json.loads((root/"docs/retrieval-before.json").read_text(encoding="utf-8"))
with httpx.Client(base_url="http://localhost:8000",headers={"X-API-Key":s["API_KEY"]},timeout=240) as client:
    checks=[]
    for question in [before["question"],"ادعى الغير ملكية المعقول"]:
        response=client.post("/search/legal",json={"question":question,"top_k":10,"debug":True})
        response.raise_for_status();result=response.json()
        assert result["sources"][0]["article_number"]=="403"
        assert result["sources"][0]["metadata"]["domain"]=="civil_procedure"
        assert not any(s["metadata"]["domain"] in ["local_tax","tax_law"] for s in result["sources"][:3])
        assert len(result["sources"])<10
        checks.append({"question":question,**result})
    # Check unchanged original articles from the captured baseline, plus retained vector count.
    unchanged=0
    for source in before["sources"]:
        row=client.get("/legal-articles/"+source["article_id"]);row.raise_for_status()
        assert row.json()["original_text"]==source["original_text"]
        unchanged+=1
    status=client.get("/assistant/status").json()
    assert status["embedded_chunks"]>=4829
(root/"docs/retrieval-after.json").write_text(json.dumps(checks[0],ensure_ascii=False,indent=2),encoding="utf-8")
(root/"docs/retrieval-arabic-check.json").write_text(json.dumps(checks[1],ensure_ascii=False,indent=2),encoding="utf-8")
lines=["# Validation du retrieval juridique", "", "Question : "+before["question"], "", "Les scores ont changé de définition : comparer le classement et les composantes, pas leur valeur absolue entre versions.", "", "| Rang | Avant : source / article | Score ancien | Après : source / article | Score métier |", "|---|---|---:|---|---:|"]
for i in range(3):
    a=before["sources"][i];b=checks[0]["sources"][i] if i<len(checks[0]["sources"]) else None
    lines.append(f"| {i+1} | {a['title']} — {a['article_number']} | {a['score']:.6f} | "+(f"{b['title']} — {b['article_number']} | {b['score']:.6f} |" if b else "Aucun passage supplémentaire | — |"))
lines += ["",f"{len(checks[0]['sources'])} sources conservées pour top_k=10. Domaine détecté : civil_procedure / enforcement_seizure.","",f"{unchanged} textes originaux du relevé avant correction sont identiques. Les 4 829 vecteurs juridiques existants sont conservés.","", "Test arabe : article 403 classé premier. Les trois tests synthétiques FR, AR et FR→AR vérifient un écart de plus de 4 points entre le passage de procédure civile et le texte fiscal répétitif.","", "Les fichiers JSON voisins détaillent les candidats, canaux, scores et raisons de sélection/rejet. Les seuils et coefficients sont heuristiques ; une évaluation sur un jeu juridique annoté reste nécessaire."]
(root/"docs/RETRIEVAL_VALIDATION.md").write_text("\n".join(lines),encoding="utf-8")
print(json.dumps({"after":[{"article":r["article_number"],"score":r["score"]} for r in checks[0]["sources"]],"originals_unchanged":unchanged,"arabic_first":"403"}))
