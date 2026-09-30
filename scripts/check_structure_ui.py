from pathlib import Path
from dotenv import dotenv_values
from playwright.sync_api import sync_playwright,expect
import json
root=Path(__file__).resolve().parents[1]
settings=dotenv_values(root/".env")
with sync_playwright() as p:
    browser=p.chromium.launch(channel="msedge",headless=True)
    page=browser.new_page(viewport={"width":1440,"height":1000});errors=[]
    page.on("pageerror",lambda e:errors.append(str(e)))
    page.goto("http://localhost:3000",wait_until="networkidle")
    page.get_by_label("Clé d’accès du cabinet").fill(settings["API_KEY"])
    page.get_by_role("button",name="Entrer dans mon espace").click()
    page.get_by_role("button",name="Bibliothèque",exact=False).click()
    expect(page.locator(".document-card").first).to_be_visible()
    summary=page.locator(".document-card details summary").filter(has_text="vecteurs").first
    expect(summary).to_be_visible();summary.click()
    expect(page.get_by_text("Sous-domaines :",exact=False).first).to_be_visible()
    page.set_viewport_size({"width":390,"height":844})
    assert page.evaluate("document.documentElement.scrollWidth<=window.innerWidth")
    assert errors==[],errors
    response=page.request.get("http://localhost:3000/api/backend/search/index/status")
    assert response.status==200
    data=response.json()
    (root/"docs/index-structure-status.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    (root/"docs/structure-browser-check.json").write_text(json.dumps({"documents":data["documents_count"],"articles":data["articles_count"],"embeddings":data["embeddings_count"],"mobile_no_overflow":True,"javascript_errors":errors},indent=2),encoding="utf-8")
    browser.close()
print("Library counts, protected proxy, mobile layout and JavaScript: OK")
