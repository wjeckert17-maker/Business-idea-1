import type { Metadata, Viewport } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = { title: "Planner", description: "Build a term schedule from your requirements" };
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

const NAV = [["/onboarding", "You"], ["/constraints", "Constraints"], ["/results", "Schedules"], ["/plan", "Register"]] as const;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-dvh">
        <main className="mx-auto max-w-lg px-4 pb-24 pt-4">{children}</main>
        <nav className="fixed inset-x-0 bottom-0 border-t" style={{ borderColor: "var(--line)", background: "var(--bg)", paddingBottom: "env(safe-area-inset-bottom)" }}>
          <ul className="mx-auto flex max-w-lg justify-around">
            {NAV.map(([href, label]) => (
              <li key={href}><Link href={href} className="block px-3 py-3 text-sm font-medium">{label}</Link></li>
            ))}
          </ul>
        </nav>
      </body>
    </html>
  );
}
