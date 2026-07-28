import type { Metadata } from "next";
import { headers } from "next/headers";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host =
    requestHeaders.get("x-forwarded-host") ??
    requestHeaders.get("host") ??
    "localhost";
  const protocol =
    requestHeaders.get("x-forwarded-proto") ??
    (host.startsWith("localhost") ? "http" : "https");
  const metadataBase = new URL(`${protocol}://${host}`);

  return {
    metadataBase,
    title: "FMCPT | Central de comandos",
    description:
      "Painel Telegram para consultar e gerenciar comandos dos bots FMCPT.",
    openGraph: {
      title: "FMCPT | Central de comandos",
      description:
        "Consulte e gerencie os comandos dos bots FMCPT em uma interface rápida.",
      type: "website",
      images: [
        {
          url: "/mini/og.png",
          width: 1536,
          height: 1024,
          alt: "FMCPT Central de comandos",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: "FMCPT | Central de comandos",
      description:
        "Consulte e gerencie os comandos dos bots FMCPT em uma interface rápida.",
      images: ["/mini/og.png"],
    },
  };
}

export default function Home() {
  return (
    <main className="redirect-page">
      <div className="redirect-mark" aria-hidden="true">
        F
      </div>
      <h1>FMCPT</h1>
      <p>Abrindo a central de comandos...</p>
      <a href="/mini/index.html">Abrir Mini App</a>
      <script
        dangerouslySetInnerHTML={{
          __html: 'window.location.replace("/mini/index.html");',
        }}
      />
    </main>
  );
}
