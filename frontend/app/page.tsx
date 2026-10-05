import DashboardShell from "@/components/DashboardShell";
import { getFiles, getHealth } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [health, files] = await Promise.all([getHealth(), getFiles()]);

  return <DashboardShell health={health} files={files} />;
}