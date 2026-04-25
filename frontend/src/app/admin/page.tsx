import type { Metadata } from "next";
import { ParsnipRuntimeProvider } from "../providers";
import { AdminShell } from "@/components/admin/AdminShell";

export const metadata: Metadata = {
  title: "Admin — pi-agent",
  description: "Administration console for pi-agent stack",
};

export default function AdminPage() {
  return (
    <ParsnipRuntimeProvider>
      <AdminShell />
    </ParsnipRuntimeProvider>
  );
}