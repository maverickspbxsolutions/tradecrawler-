import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, TriangleAlert } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { SpiderIcon } from "@/components/SpiderIcon";
import { LandingChart } from "@/components/LandingChart";
import { formatApiErrorDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function Login() {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState("login"); // login | register
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [licenseKey, setLicenseKey] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      if (mode === "register") {
        await register(email, password, licenseKey);
      } else {
        await login(username, password);
      }
      navigate("/");
    } catch (err) {
      setError(formatApiErrorDetail(err.response?.data?.detail) || "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen grid lg:grid-cols-2 bg-background">
      <div className="relative hidden lg:block overflow-hidden border-r border-border/60 bg-background">
        <div className="absolute inset-0 opacity-70">
          <LandingChart />
        </div>
        <div className="absolute inset-0 bg-gradient-to-t from-background via-background/50 to-transparent pointer-events-none" />
        <div className="absolute bottom-0 left-0 p-10 pointer-events-none">
          <div className="text-xs font-mono uppercase tracking-[0.25em] text-primary mb-3">crypto trade alerts · 24/7</div>
          <h1 className="text-4xl font-semibold tracking-tight leading-tight max-w-md">
            TradeCrawler finds the trade.<br />You just check your email.
          </h1>
          <p className="text-sm text-muted-foreground mt-4 max-w-sm leading-relaxed">
            It watches crypto around the clock and emails you a simple plan — what to buy for about $20, and when to sell. No charts to read, no guessing.
          </p>
        </div>
      </div>

      <div className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm" data-testid="login-form">
          <div className="flex items-center gap-3 mb-8">
            <div className="h-10 w-10 grid place-items-center border border-primary/40 bg-primary/10 rounded-sm">
              <SpiderIcon className="h-6 w-6 text-primary" />
            </div>
            <div>
              <div className="text-lg font-semibold tracking-tight">TradeCrawler</div>
              <div className="text-[10px] font-mono uppercase tracking-[0.15em] text-muted-foreground">Sign in to your crawler</div>
            </div>
          </div>

          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-1 p-1 bg-muted/40 rounded-sm mb-1">
              {["login", "register"].map((m) => (
                <button key={m} type="button" onClick={() => { setMode(m); setError(""); }}
                  data-testid={`tab-${m}`}
                  className={`py-1.5 rounded-sm text-xs font-mono uppercase tracking-wider transition-colors ${
                    mode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}>
                  {m === "login" ? "Sign in" : "Activate key"}
                </button>
              ))}
            </div>

            {mode === "login" ? (
              <div>
                <Label className="text-[10px] font-mono uppercase text-muted-foreground">Email or Username</Label>
                <Input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username"
                  className="h-10 rounded-sm mt-1" data-testid="username-input" />
              </div>
            ) : (
              <>
                <div>
                  <Label className="text-[10px] font-mono uppercase text-muted-foreground">Email (where alerts are sent)</Label>
                  <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email"
                    className="h-10 rounded-sm mt-1" data-testid="email-input" />
                </div>
                <div>
                  <Label className="text-[10px] font-mono uppercase text-muted-foreground">License Key</Label>
                  <Input value={licenseKey} onChange={(e) => setLicenseKey(e.target.value.toUpperCase())}
                    placeholder="TC-XXXX-XXXX-XXXX-XXXX" className="h-10 rounded-sm mt-1 font-mono" data-testid="license-input" />
                </div>
              </>
            )}
            <div>
              <Label className="text-[10px] font-mono uppercase text-muted-foreground">Password</Label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "register" ? "new-password" : "current-password"}
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
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : mode === "register" ? "Activate & Enter" : "Enter Terminal"}
            </Button>
          </div>

          <p className="text-[10px] text-muted-foreground font-mono mt-6 leading-relaxed">
            TradeCrawler can place trades on a Deriv MT5 demo account — but only on your explicit approval, with virtual funds. Not financial advice.
          </p>
        </form>
      </div>
    </div>
  );
}
