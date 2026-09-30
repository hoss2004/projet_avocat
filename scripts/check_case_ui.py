"""End-to-end local worker/UI smoke test using a clearly labelled synthetic case."""
from pathlib import Path
from uuid import uuid4
import json,time
from dotenv import dotenv_values
from playwright.sync_api import sync_playwright,expect
root=Path(__file__).resolve().parents[1]
settings=dotenv_values(root/".env")
ref="TEST-E2E-"+uuid4().hex[:8]
folder=root/".tmp";folder.mkdir(exist_ok=True)
first=folder/"TEST contrat.txt";second=folder/"TEST requete.txt"
first.write_text("TEST CASE — SYNTHETIC, NO LEGAL EFFECT.\nClient : TEST CLIENT.\nSignature du contrat : 02/03/2025.\nUn tiers affirme être propriétaire du bien saisi. Il souhaite contester la saisie.\n",encoding="utf-8")
second.write_text("TEST CASE — SYNTHETIC, NO LEGAL EFFECT.\nPartie adverse : TEST OPPONENT.\nSignature du contrat : 12/03/2025.\nLe paiement est allégué mais aucun justificatif n'est joint.\n",encoding="utf-8")
with sync_playwright() as p:
    browser=p.chromium.launch(channel="msedge",headless=True)
    context=browser.new_context(viewport={"width":1440,"height":1000})
    page=context.new_page();errors=[];page.on("pageerror",lambda e:errors.append(str(e)))
    page.goto("http://localhost:3000",wait_until="networkidle")
    page.get_by_label("Clé d’accès du cabinet").fill(settings["API_KEY"])
    page.get_by_role("button",name="Entrer dans mon espace").click()
    page.get_by_role("button",name="Dossiers",exact=False).click()
    page.get_by_label("Nom du dossier (facultatif)").fill("DOSSIER DE TEST AUTOMATIQUE")
    page.get_by_label("Référence (facultative)").fill(ref)
    page.get_by_label("Client du cabinet",exact=True).fill("TEST CLIENT")
    page.get_by_label("Partie adverse",exact=True).fill("TEST OPPONENT")
    page.get_by_role("button",name="Créer le dossier",exact=True).click()
    expect(page.get_by_label("Dossier actif",exact=True)).not_to_have_value("")
    case_id=page.get_by_label("Dossier actif",exact=True).input_value()
    (folder/"case-test-id.json").write_text(json.dumps({"case_id":case_id,"reference":ref}),encoding="utf-8")
    page.get_by_label("Pièces du dossier",exact=True).set_input_files([str(first),str(second)])
    expect(page.locator(".case-pieces .document-card")).to_have_count(2,timeout=60000)
    page.get_by_role("button",name="Analyser entièrement le dossier",exact=True).click()
    start=time.monotonic()
    page.get_by_role("heading",name="Rapport d’analyse provisoire",exact=True).wait_for(timeout=360000)
    report=context.request.get(f"http://localhost:3000/api/backend/cases/{case_id}/analysis").json()
    assert report["status"] in {"completed","partial"},report
    assert len(report["report"]["sections"])==24
    assert report["report"]["coverage"]["documents_total"]==2
    assert report["report"]["coverage"]["chunks_read"]==report["report"]["coverage"]["chunks_total"]
    page.screenshot(path=str(folder/"case-report.png"),full_page=True)
    page.get_by_label("Type de document à générer",exact=True).select_option("PREPARE_HEARING")
    page.get_by_role("button",name="Générer le brouillon",exact=True).click()
    page.get_by_role("button",name="Télécharger le brouillon",exact=True).wait_for(timeout=240000)
    with page.expect_download() as download:
        page.get_by_role("button",name="Télécharger le brouillon",exact=True).click()
    download.value.save_as(str(folder/"test-brouillon.md"))
    assert "BROUILLON" in (folder/"test-brouillon.md").read_text(encoding="utf-8")
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors,errors
    (root/"docs"/"case-browser-check.json").write_text(json.dumps({"case_id":case_id,"reference":ref,"status":report["status"],"sections":24,"coverage":report["report"]["coverage"],"draft_download":True,"mobile_no_overflow":True,"javascript_errors":errors,"elapsed_seconds":round(time.monotonic()-start,2)},indent=2),encoding="utf-8")
    print("Case workflow verified: multi-upload, persistent worker, report, draft download and mobile layout.")
    browser.close()
