import { Config } from "@remotion/cli/config";

Config.setEntryPoint("./src/index.ts");

// El motor Python decide el codec; aqui solo se fijan valores de trabajo.
// FFmpeg se encarga de trim/encode/mux de media (seccion 29). Remotion compone.
Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setConcurrency(1);
