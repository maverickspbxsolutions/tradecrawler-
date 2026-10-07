import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined); // undefined = loading, false = no auth

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      setUser(false);
      return;
    }
    api
      .get("/auth/me")
      .then((r) => setUser(r.data))
      .catch(() => {
        localStorage.removeItem("token");
        setUser(false);
      });
  }, []);

  const login = async (username, password) => {
    const { data } = await api.post("/auth/login", { username, password });
    localStorage.setItem("token", data.token);
    setUser(data.user);
    return data.user;
  };

  const register = async (email, password, license_key) => {
    const { data } = await api.post("/auth/register", { email, password, license_key });
    localStorage.setItem("token", data.token);
    setUser(data.user);
    return data.user;
  };

  const redeem = async (key) => {
    const { data } = await api.post("/account/redeem", { key });
    const me = await api.get("/auth/me");
    setUser(me.data);
    return data;
  };

  const refreshUser = async () => {
    const me = await api.get("/auth/me");
    setUser(me.data);
    return me.data;
  };

  const logout = () => {
    localStorage.removeItem("token");
    setUser(false);
  };

  return <AuthContext.Provider value={{ user, login, register, redeem, refreshUser, logout }}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
