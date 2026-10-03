import express from "express";
import { configureProxy } from "./bootstrap/proxy";
import { sessionMiddleware } from "./config/session";
import { authRouter } from "./routes/auth";

const app = express();
configureProxy(app);
app.use(express.json());
app.use(sessionMiddleware);
app.use("/auth", authRouter);

app.listen(Number(process.env.PORT ?? 3000));
