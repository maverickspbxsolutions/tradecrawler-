import { useQuery } from "@tanstack/react-query";
import { LogOut, Settings, Radio, Database, Sun, Moon } from "lucide-react";
import { useTheme } from "next-themes";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { SpiderIcon } from "@/components/SpiderIcon";
import { Button } from "@/components/ui/button";
import { AdminKeys } from "@/components/AdminKeys";

export function TopBar({ onOpenSettings }) {
  const { user, logout } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  const isLight = resolvedTheme === "light";
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: async () => (await api.get("/market/health")).data,
    refetchInterval: 20000,
  });

  const stale = health?.stale;
  const source = health?.source || "—";

  return (
    <header className="sticky top-[26px] z-40 border-b border-border/60 bg-background">
      <div className="w-full max-w-[1920px] mx-auto px-4 sm:px-6 h-14 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="h-8 w-8 grid place-items-center border border-primary/40 bg-primary/10 rounded-sm">
            <SpiderIcon className="h-5 w-5 text-primary" />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-tight">TradeCrawler</div>
            <div className="text-[10px] font-mono uppercase tracking-[0.15em] text-muted-foreground">
              crypto trade alerts
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 sm:gap-4">
          <div className="hidden sm:flex items-center gap-4 text-[10px] font-mono uppercase tracking-[0.12em]">
            <span className="flex items-center gap-1.5 text-muted-foreground" data-testid="data-source">
              <Database className="h-3 w-3" /> {source}
            </span>
            <span
              className={`flex items-center gap-1.5 ${stale ? "text-sell" : "text-buy"}`}
              data-testid="engine-status"
            >
              <Radio className="h-3 w-3" /> {stale ? "data delayed" : "watching 24/7"}
            </span>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2 rounded-sm"
            onClick={() => setTheme(isLight ? "dark" : "light")}
            data-testid="theme-toggle-btn"
            title={isLight ? "Switch to dark" : "Switch to light"}
          >
            {isLight ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2 rounded-sm"
            onClick={onOpenSettings}
            data-testid="open-settings-btn"
          >
            <Settings className="h-4 w-4" />
          </Button>
          {user?.license?.is_admin && <AdminKeys />}
          <div className="hidden sm:block text-xs text-muted-foreground font-mono">{user?.username}</div>
          <Button
            variant="outline"
            size="sm"
            className="h-8 rounded-sm border-border"
            onClick={logout}
            data-testid="logout-btn"
          >
            <LogOut className="h-3.5 w-3.5 mr-1.5" /> Exit
          </Button>
        </div>
      </div>
    </header>
  );
}
