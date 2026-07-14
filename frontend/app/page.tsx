// frontend/app/page.tsx
// -----------------------------------------------------------------------------
// Structure globale du dashboard.
//
// Server Component : récupère /health et /files côté serveur avant le premier
// rendu (pas de spinner de chargement initial pour ces données). Les parties
// interactives (chat, upload) sont déléguées à des Client Components importés
// ci-dessous.
//
// DIRECTION DE DESIGN :
// L'en-tête reprend le vocabulaire d'une bannière de commande VRP
// ("<NetRAG> display system-status") — clin d'œil direct au terminal Huawei
// que l'utilisateur manipule déjà, plutôt qu'un habillage SaaS générique.
// -----------------------------------------------------------------------------

import ChatInterface from "@/components/ChatInterface";
import UploadZone from "@/components/UploadZone";
import { getFiles, getHealth } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [health, files] = await Promise.all([getHealth(), getFiles()]);

  const overallDotClass =
    health.status === "ok" ? "status-dot--live" : "status-dot--warn";

  const devicesLabel =
    health.devices_total != null
      ? `${health.devices_reachable ?? 0}/${health.devices_total} équipements joignables`
      : "topologie non chargée";

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        padding: "1.5rem",
        gap: "1.25rem",
        maxWidth: "1400px",
        margin: "0 auto",
      }}
    >
      {/* ---------- Bannière façon prompt VRP ---------- */}
      <header
        style={{
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          background: "var(--surface)",
          padding: "0.85rem 1.25rem",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: "0.75rem",
          fontFamily: "var(--font-mono)",
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: "0.6rem" }}>
          <span style={{ color: "var(--accent-live)", fontWeight: 600 }}>
            &lt;NetRAG&gt;
          </span>
          <span style={{ color: "var(--text-dim)", fontSize: "0.9rem" }}>
            display system-status
          </span>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "1.25rem",
            fontSize: "0.8rem",
            color: "var(--text-dim)",
          }}
        >
          <span style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
            <span className={`status-dot ${overallDotClass}`} />
            {health.status === "ok" ? "pipeline opérationnel" : "pipeline dégradé"}
          </span>
          <span>{devicesLabel}</span>
          <span
            style={{
              display: "flex",
              alignItems: "center",
              gap: "0.4rem",
            }}
          >
            <span
              className={`status-dot ${
                health.qdrant_connected ? "status-dot--live" : "status-dot--offline"
              }`}
            />
            Qdrant
          </span>
        </div>
      </header>

      {/* ---------- Grille principale : chat + rail latéral ---------- */}
      <div
        style={{
          flex: 1,
          display: "grid",
          gridTemplateColumns: "minmax(0, 2fr) minmax(280px, 1fr)",
          gap: "1.25rem",
        }}
        className="dashboard-grid"
      >
        <section style={{ minHeight: "60vh" }}>
          <ChatInterface />
        </section>

        <aside
          style={{
            display: "flex",
            flexDirection: "column",
            gap: "1rem",
          }}
        >
          <div>
            <SectionLabel>Ingestion</SectionLabel>
            <UploadZone />
          </div>

          <div>
            <SectionLabel>
              Documents indexés
              {files.length > 0 ? ` (${files.length})` : ""}
            </SectionLabel>
            <FilesList files={files} />
          </div>
        </aside>
      </div>

      {/* Empile chat + rail sur mobile plutôt que colonnes côte à côte */}
      <style>{`
        @media (max-width: 860px) {
          .dashboard-grid {
            grid-template-columns: 1fr !important;
          }
        }
      `}</style>
    </main>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "0.72rem",
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        color: "var(--text-faint)",
        marginBottom: "0.5rem",
      }}
    >
      {children}
    </div>
  );
}

function FilesList({
  files,
}: {
  files: Awaited<ReturnType<typeof getFiles>>;
}) {
  if (files.length === 0) {
    return (
      <p
        style={{
          fontSize: "0.82rem",
          color: "var(--text-faint)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          padding: "0.85rem",
        }}
      >
        Aucun document indexé pour l&apos;instant. Dépose un PDF Huawei
        ci-dessus pour l&apos;ajouter à la base Qdrant.
      </p>
    );
  }

  return (
    <ul
      style={{
        listStyle: "none",
        margin: 0,
        padding: 0,
        display: "flex",
        flexDirection: "column",
        gap: "0.5rem",
      }}
    >
      {files.map((file) => (
        <li
          key={file.file_id}
          style={{
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            background: "var(--surface)",
            padding: "0.6rem 0.75rem",
            fontSize: "0.82rem",
          }}
        >
          <div style={{ color: "var(--text)" }}>{file.filename}</div>
          <div
            style={{
              color: "var(--text-faint)",
              fontFamily: "var(--font-mono)",
              fontSize: "0.7rem",
              marginTop: "0.15rem",
            }}
          >
            {file.chunks_indexed != null
              ? `${file.chunks_indexed} chunks`
              : "indexation en cours"}
          </div>
        </li>
      ))}
    </ul>
  );
}