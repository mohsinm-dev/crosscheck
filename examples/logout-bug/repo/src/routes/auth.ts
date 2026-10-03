import { Router } from "express";

export const authRouter = Router();

authRouter.post("/login", (req, res) => {
  // credential check elided
  req.session.userId = req.body.userId;
  res.json({ ok: true });
});

authRouter.post("/logout", (req, res) => {
  req.session.destroy(() => res.json({ ok: true }));
});

authRouter.get("/me", (req, res) => {
  if (!req.session.userId) return res.status(401).json({ error: "not logged in" });
  res.json({ userId: req.session.userId });
});
