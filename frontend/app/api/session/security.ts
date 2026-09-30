import { createHmac, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";
export const COOKIE = "legal_session";
function signature(value: string) { return createHmac("sha256", process.env.API_KEY || "unconfigured").update(value).digest("hex"); }
export function safeEqual(a: string, b: string) { const x=Buffer.from(a), y=Buffer.from(b); return x.length===y.length && timingSafeEqual(x,y); }
export function issueToken() { const expiry=String(Date.now()+8*60*60*1000); return expiry+"."+signature(expiry); }
export async function authenticated() { const token=(await cookies()).get(COOKIE)?.value || ""; const [expiry,sig]=token.split("."); return !!process.env.API_KEY && !!sig && Number(expiry)>Date.now() && safeEqual(sig,signature(expiry)); }
export function sameOrigin(request: Request) {
 const origin=request.headers.get("origin");
 // Standalone Next uses its internal container address for request.url.
 const allowed=(process.env.APP_ORIGINS || "http://localhost:3000,http://127.0.0.1:3000").split(",").map(s=>s.trim());
 return !!origin && allowed.includes(origin);
}
