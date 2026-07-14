// frontend/app/layout.tsx
import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import "./globals.css";

// Mono = vocabulaire terminal VRP (bannière, statuts, données device).
const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

// Sans = lecture longue (réponses du Documentaliste/Validateur).
const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "NetRAG — Assistant réseau Huawei",
  description:
    "Assistant RAG multimodal pour la documentation et l'infrastructure réseau Huawei.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="fr">
      <body className={`${plexMono.variable} ${plexSans.variable}`}>
        {children}
      </body>
    </html>
  );
}