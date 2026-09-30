import { NextResponse } from "next/server";
import { authenticated, sameOrigin } from "../../session/security";
export const runtime="nodejs";
async function proxy(request: Request, context: {params: Promise<{path:string[]}>}) {
 if(!await authenticated()) return NextResponse.json({detail:"Veuillez vous connecter"},{status:401});
 if(request.method!=="GET" && !sameOrigin(request)) return NextResponse.json({detail:"Origine refusée"},{status:403});
 const {path}=await context.params;
 const allowed=["legal-documents","legal-articles","cases","search","rag","assistant"];
 if(!allowed.includes(path[0]) || path.some(p=>!p || p===".." || p.includes("/"))) return NextResponse.json({detail:"Route inconnue"},{status:404});
 const headers: Record<string,string>={"X-API-Key":process.env.API_KEY || ""};
 const contentType=request.headers.get("content-type"); if(contentType) headers["Content-Type"]=contentType;
 let body: Uint8Array | undefined;
 if(request.method!=="GET" && request.body) {
   const reader=request.body.getReader(); const parts:Uint8Array[]=[]; let size=0;
   while(true) {const {done,value}=await reader.read(); if(done) break; size+=value.length; if(size>27*1024*1024) {await reader.cancel();return NextResponse.json({detail:"Fichier trop volumineux (25 Mo maximum)"},{status:413});} parts.push(value);}
   body=new Uint8Array(size); let offset=0; for(const part of parts){body.set(part,offset);offset+=part.length;}
 }
 try {
   const response=await fetch((process.env.BACKEND_URL || "http://backend:8000")+"/"+path.map(encodeURIComponent).join("/")+new URL(request.url).search,{method:request.method,headers,body:body as BodyInit|undefined,signal:AbortSignal.timeout(240000),cache:"no-store"});
   const out=new Headers({"Cache-Control":"no-store","Content-Type":response.headers.get("content-type") || "application/json"});
   const disposition=response.headers.get("content-disposition");if(disposition)out.set("Content-Disposition",disposition);
   return new Response(response.body,{status:response.status,headers:out});
 } catch {return NextResponse.json({detail:"Le serveur ne répond pas. Réessayez après quelques instants."},{status:503});}
}
export {proxy as GET,proxy as POST};
