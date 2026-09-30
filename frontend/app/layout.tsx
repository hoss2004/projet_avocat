import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {title: "Legal AI Tunisia — Votre espace juridique", description: "Recherche juridique et analyse documentaire avec sources."};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {return <html lang="fr"><body>{children}</body></html>}
