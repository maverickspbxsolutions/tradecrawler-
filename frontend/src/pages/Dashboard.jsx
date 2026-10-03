import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Radar, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { DisclaimerBanner } from "@/components/DisclaimerBanner";
import { TopBar } from "@/components/TopBar";
import { StatsBar } from "@/components/StatsBar";
import { PriceChart } from "@/components/PriceChart";
import { SignalFeed } from "@/components/SignalFeed";
import { StrategyList } from "@/components/StrategyList";
import { BacktestPanel } from "@/components/BacktestPanel";
import { PaperPanel } from "@/components/PaperPanel";
import { StrategyFormDialog } from "@/components/StrategyFormDialog";
import { SettingsDialog } from "@/components/SettingsDialog";
import { EvolutionPanel } from "@/components/EvolutionPanel";
import { Button } from "@/components/ui/button";

export default function Dashboard() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [editStrategy, setEditStrategy] = useState(null);

  const { data: strategies } = useQuery({
    queryKey: ["strategies"],
    queryFn: async () => (await api.get("/strategies")).data,
    refetchInterval: 30000,
  });

  useEffect(() => {
    if (!selectedId && strategies?.length) setSelectedId(strategies[0].id);
  }, [strategies, selectedId]);

  const selected = (strategies || []).find((s) => s.id === selectedId) || null;

  const scanAll = useMutation({
    mutationFn: () => api.post("/scan-all"),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["signals"] });
      qc.invalidateQueries({ queryKey: ["strategies"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
      const n = r.data.signals?.length || 0;
      toast.message(n ? `${n} new signal(s)` : "No new signals on latest closed candles");
    },
  });

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
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-xl font-semibold tracking-tight">Dashboard</h2>
            <p className="text-[11px] text-muted-foreground font-mono">
              Signals computed on closed candles only · no repainting · no lookahead
            </p>
          </div>
          <Button
            className="rounded-sm bg-primary text-primary-foreground hover:bg-primary/90 h-9"
            onClick={() => scanAll.mutate()}
            disabled={scanAll.isPending}
            data-testid="scan-all-btn"
          >
            {scanAll.isPending ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : <Radar className="h-4 w-4 mr-1.5" />}
            Scan All Now
          </Button>
        </div>

        <StatsBar />

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-3">
          <div className="lg:col-span-8 space-y-3">
            <PriceChart symbol={selected?.symbol || "BTCUSDT"} timeframe={selected?.timeframe || "1h"} />
            <SignalFeed />
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
          Signals are not financial advice · Paper-trading only · No orders placed
        </footer>
      </main>

      <StrategyFormDialog open={formOpen} onOpenChange={setFormOpen} strategy={editStrategy} />
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  );
}
