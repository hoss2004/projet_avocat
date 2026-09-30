import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { COOKIE, authenticated, safeEqual, issueToken, sameOrigin } from "./security";
export async function GET() { return NextResponse.json({authenticated:await authenticated()}); }
export async function POST(request: Request) {
  if (!sameOrigin(request)) return NextResponse.json({detail:"Origine refusée"},{status:403});
  const body=await request.text();
  if(body.length>2048) return NextResponse.json({detail:"Requête trop longue"},{status:413});
  let key=""; try {key=JSON.parse(body).key || "";} catch {return NextResponse.json({detail:"Requête invalide"},{status:400});}
  if(typeof key!=="string" || !process.env.API_KEY || !safeEqual(key,process.env.API_KEY)) { await new Promise(r=>setTimeout(r,800)); return NextResponse.json({detail:"Clé d’accès incorrecte"},{status:401}); }
  (await cookies()).set(COOKIE,issueToken(),{httpOnly:true,sameSite:"strict",secure:new URL(request.url).protocol==="https:",maxAge:28800,path:"/"});
  return NextResponse.json({authenticated:true});
}
export async function DELETE(request: Request) {
 if(!sameOrigin(request)) return NextResponse.json({detail:"Origine refusée"},{status:403});
 (await cookies()).delete(COOKIE); return NextResponse.json({authenticated:false});
}
