import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Activity, Loader2, TriangleAlert } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { formatApiErrorDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const HERO =
  "https://images.unsplash.com/photo-1763888450809-4125d93fa56b?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NTY2OTF8MHwxfHNlYXJjaHwxfHxhYnN0cmFjdCUyMHRyYWRpbmclMjBjaGFydCUyMGRhcmslMjBiYWNrZ3JvdW5kfGVufDB8fHx8MTc5MTA1MDI3NXww&ixlib=rb-4.1.0&q=85";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
      navigate("/");
    } catch (err) {
      setError(formatApiErrorDetail(err.response?.data?.detail) || "Login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen grid lg:grid-cols-2 bg-background">
      <div className="relative hidden lg:block overflow-hidden border-r border-border/60">
        <img src={HERO} alt="" className="absolute inset-0 h-full w-full object-cover opacity-60" />
        <div className="absolute inset-0 bg-gradient-to-t from-background via-background/40 to-transparent" />
        <div className="absolute bottom-0 left-0 p-10">
          <div className="text-xs font-mono uppercase tracking-[0.25em] text-primary mb-3">Crypto · alerts-only</div>
          <h1 className="text-4xl font-semibold tracking-tight leading-tight max-w-md">
            A disciplined signal engine.<br />Not a predictor.
          </h1>
          <p className="text-sm text-muted-foreground mt-4 max-w-sm leading-relaxed">
            Rule-based signals, honest backtests with fees &amp; slippage, and live paper-trading before anything goes live.
          </p>
        </div>
      </div>

      <div className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm" data-testid="login-form">
          <div className="flex items-center gap-3 mb-8">
            <div className="h-10 w-10 grid place-items-center border border-primary/40 bg-primary/10 rounded-sm">
              <Activity className="h-5 w-5 text-primary" />
            </div>
            <div>
              <div className="text-lg font-semibold tracking-tight">Signal Terminal</div>
              <div className="text-[10px] font-mono uppercase tracking-[0.15em] text-muted-foreground">Secure sign in</div>
            </div>
          </div>

          <div className="space-y-4">
            <div>
              <Label className="text-[10px] font-mono uppercase text-muted-foreground">Username</Label>
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                className="h-10 rounded-sm mt-1"
                data-testid="username-input"
              />
            </div>
            <div>
              <Label className="text-[10px] font-mono uppercase text-muted-foreground">Password</Label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className="h-10 rounded-sm mt-1"
                data-testid="password-input"
              />
            </div>

            {error && (
              <div className="flex items-center gap-2 text-xs text-sell font-mono border border-sell/40 bg-sell/5 rounded-sm px-3 py-2" data-testid="login-error">
                <TriangleAlert className="h-3.5 w-3.5" /> {error}
              </div>
            )}

            <Button
              type="submit"
              disabled={loading}
              className="w-full h-10 rounded-sm bg-primary text-primary-foreground hover:bg-primary/90 font-medium"
              data-testid="login-submit-btn"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Enter Terminal"}
            </Button>
          </div>

          <p className="text-[10px] text-muted-foreground font-mono mt-6 leading-relaxed">
            Signals are not financial advice. This tool places no orders and connects to no trading account.
          </p>
        </form>
      </div>
    </div>
  );
}
