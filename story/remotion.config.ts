import { Config } from '@remotion/cli/config';

// JPEG frames are smaller and faster to hand to the encoder; the grain and the grade hide any loss.
Config.setVideoImageFormat('jpeg');
Config.setJpegQuality(92);
Config.setOverwriteOutput(true);
