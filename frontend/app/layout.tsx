import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import "./globals.css";
import { ThemeProvider } from "./theme-provider";


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

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0f0f0f" },
  ],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="fr" suppressHydrationWarning>
      <body className={`${plexMono.variable} ${plexSans.variable}`}>
        <ThemeProvider>
          <div className="signal-bar" aria-hidden="true" />
          {children}
        </ThemeProvider>
      </body>
    </html>
  );

}