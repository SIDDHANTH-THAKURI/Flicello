import { Config } from '@remotion/cli/config';

// JPEG frames are smaller and faster to hand to the encoder; the grain and the grade hide any loss.
Config.setVideoImageFormat('jpeg');
Config.setJpegQuality(92);
Config.setOverwriteOutput(true);

// The media a plan names lives outside the repo (the repo sits in a synced folder): see tools/prepare.py.
const local = process.env.VIDO_STORY_PUBLIC ?? `${process.env.LOCALAPPDATA ?? process.env.HOME}/VidoSuper/story/public`;
Config.setPublicDir(local);
