import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

import { AppSidebar } from "@/components/app-sidebar";
import { ChatPanel } from "@/components/chat-panel";
import { CommandPalette } from "@/components/command-palette";
import { SymbolRouteSync } from "@/components/symbol-route-sync";
import { TooltipProvider } from "@/components/ui/tooltip";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Vol Dashboard",
  description: "Options-volatility intelligence: term structure, skew, curvature, and decision support.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // `dark` is applied unconditionally rather than following the OS setting:
    // this is a trading terminal that gets stared at for hours, the palette is
    // tuned specifically for a near-black ground, and a surprise light render
    // on someone's daytime machine would show colours that were never designed
    // against white. One deliberate look, not two half-maintained ones.
    <html
      lang="en"
      className={`dark ${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="h-full">
        <TooltipProvider delayDuration={150}>
          <SymbolRouteSync />
          <CommandPalette />
          <div className="flex h-screen overflow-hidden bg-background">
            <AppSidebar />
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden">{children}</div>
          </div>
          <ChatPanel />
        </TooltipProvider>
      </body>
    </html>
  );
}
