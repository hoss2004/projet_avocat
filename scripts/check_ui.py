"""Local browser smoke test; never prints credentials."""
from pathlib import Path
import json
from dotenv import dotenv_values
from playwright.sync_api import sync_playwright, expect

root=Path(__file__).resolve().parents[1]
settings=dotenv_values(root/".env")
output=root/".tmp"
output.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(channel="msedge",headless=True)
    context=browser.new_context(viewport={"width":1440,"height":1000})
    page=context.new_page()
    errors=[]
    page.on("pageerror",lambda error:errors.append(str(error)))
    page.goto("http://localhost:3000",wait_until="networkidle")
    page.get_by_role("heading",name="Bienvenue").wait_for()
    page.screenshot(path=str(output/"login.png"),full_page=True)
    assert context.request.get("http://localhost:3000/api/backend/cases").status==401
    page.get_by_label("Clé d’accès du cabinet").fill(settings["API_KEY"])
    page.get_by_role("button",name="Entrer dans mon espace").click()
    page.get_by_role("heading",name="Votre assistant juridique").wait_for(timeout=30000)
    assert any(c["name"]=="legal_session" and c["httpOnly"] for c in context.cookies())
    assert page.evaluate("Object.keys(localStorage).length")==0
    assert context.request.post("http://localhost:3000/api/backend/search/index",headers={"Origin":"http://untrusted.invalid"}).status==403
    page.get_by_role("button",name="Bibliothèque",exact=False).first.click()
    page.get_by_role("heading",name="Bibliothèque juridique",exact=True).wait_for()
    expect(page.locator(".document-card").nth(17)).to_be_visible(timeout=30000)
    page.screenshot(path=str(output/"library.png"),full_page=True)
    page.get_by_role("button",name="Assistant juridique",exact=False).first.click()
    page.get_by_role("heading",name="Votre assistant juridique").wait_for()
    docs=context.request.get("http://localhost:3000/api/backend/legal-documents?limit=200").json()
    selected=next(d for d in docs if d["filename"]=="Code_de_procedure_civile_et_commerciale.pdf")
    page.get_by_label("Texte juridique",exact=True).select_option(selected["id"])
    page.get_by_label("Type d’analyse",exact=True).select_option("QUICK_ANSWER")
    page.get_by_label("Votre question",exact=True).fill("Retrouve l’article 403. Indique seulement son numéro et le titre du code, sans interprétation.")
    with page.expect_response(lambda r:r.url.endswith("/rag/query"),timeout=240000) as pending:
        page.get_by_role("button",name="Envoyer",exact=False).click()
    reply=pending.value.json()
    assert reply["mode"]=="generated",reply["warnings"]
    assert reply["claims"] and reply["citations"]
    page.locator(".message.assistant").wait_for(timeout=240000)
    page.locator(".source-card").first.wait_for(timeout=10000)
    page.locator(".source-card").first.click()
    page.locator(".source-original").wait_for()
    page.screenshot(path=str(output/"chat.png"),full_page=True)
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(output/"mobile.png"),full_page=True)
    assert not errors,errors
    (root/"docs"/"browser-check.json").write_text(json.dumps({"login":True,"protected_proxy":True,"http_only_session":True,"library_documents":len(docs),"qwen_response_mode":reply["mode"],"verified_claims":len(reply["claims"]),"source_page":reply["retrieved_sources"][0]["page_start"],"mobile_no_overflow":True,"javascript_errors":errors},indent=2),encoding="utf-8")
    print("UI verified: login, protected proxy, HttpOnly session, library, sourced chat, mobile layout; no JavaScript errors.")
    browser.close()
