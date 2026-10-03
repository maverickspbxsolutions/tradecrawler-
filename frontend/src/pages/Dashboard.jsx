import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { DisclaimerBanner } from "@/components/DisclaimerBanner";
import { TopBar } from "@/components/TopBar";
import { HowItWorks } from "@/components/HowItWorks";
import { StatsBar } from "@/components/StatsBar";
import { PriceChart } from "@/components/PriceChart";
import { MarketOutlook } from "@/components/MarketOutlook";
import { SignalFeed } from "@/components/SignalFeed";
import { MiniSimulations } from "@/components/MiniSimulations";
import { StrategyList } from "@/components/StrategyList";
import { BacktestPanel } from "@/components/BacktestPanel";
import { PaperPanel } from "@/components/PaperPanel";
import { StrategyFormDialog } from "@/components/StrategyFormDialog";
import { SettingsDialog } from "@/components/SettingsDialog";
import { EvolutionPanel } from "@/components/EvolutionPanel";

export default function Dashboard() {
  const [selectedId, setSelectedId] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editStrategy, setEditStrategy] = useState(null);
  const [tf, setTf] = useState("1h");

  const { data: strategies } = useQuery({
    queryKey: ["strategies"],
    queryFn: async () => (await api.get("/strategies")).data,
    refetchInterval: 30000,
  });

  useEffect(() => {
    if (!selectedId && strategies?.length) setSelectedId(strategies[0].id);
  }, [strategies, selectedId]);

  const selected = (strategies || []).find((s) => s.id === selectedId) || null;

  useEffect(() => {
    if (selected?.timeframe) setTf(selected.timeframe);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const openNew = () => {
    setEditStrategy(null);
    setFormOpen(true);
  };
  const openEdit = (s) => {
    setEditStrategy(s);
    setFormOpen(true);
  };

  return (
    <div className="App min-h-screen bg-background">
      <DisclaimerBanner />
      <TopBar onOpenSettings={() => setSettingsOpen(true)} />

      <main className="w-full max-w-[1920px] mx-auto px-4 sm:px-6 py-4">
        <HowItWorks onOpenSettings={() => setSettingsOpen(true)} />

        <StatsBar />

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
          <div className="lg:col-span-8 space-y-3">
            <PriceChart symbol={selected?.symbol || "BTCUSDT"} tf={tf} onTfChange={setTf} />
            <MarketOutlook symbol={selected?.symbol || "BTCUSDT"} tf={tf} />
            <SignalFeed />
            <MiniSimulations strategy={selected} />
          </div>
          <div className="lg:col-span-4">
            <StrategyList
              strategies={strategies}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onEdit={openEdit}
              onNew={openNew}
            />
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3">
          <BacktestPanel strategy={selected} />
          <PaperPanel strategy={selected} />
        </div>

        <div className="mt-3">
          <EvolutionPanel strategy={selected} />
        </div>

        <footer className="mt-6 py-4 border-t border-border/40 text-[10px] font-mono text-muted-foreground text-center uppercase tracking-[0.15em]">
          TradeCrawler sends suggestions only · not financial advice · no orders are ever placed
        </footer>
      </main>

      <StrategyFormDialog open={formOpen} onOpenChange={setFormOpen} strategy={editStrategy} />
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  );
}
