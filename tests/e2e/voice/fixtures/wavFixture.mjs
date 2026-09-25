import fs from "node:fs";
import path from "node:path";

const SAMPLE_RATE = 16_000;

export function ensureVoiceFixtureWav(
  outputDir,
  name = "voice-fixture-1s.wav",
  sourcePath = process.env.VOICE_TEST_AUDIO_FIXTURE,
) {
  if (sourcePath) {
    const resolvedSourcePath = path.resolve(sourcePath);
    if (!fs.existsSync(resolvedSourcePath)) {
      throw new Error("VOICE_TEST_AUDIO_FIXTURE does not exist: " + resolvedSourcePath);
    }
    return resolvedSourcePath;
  }
  const fixtureDir = path.join(outputDir, "fixtures");
  fs.mkdirSync(fixtureDir, { recursive: true });
  const outputPath = path.join(fixtureDir, name);
  if (!fs.existsSync(outputPath)) {
    const sampleCount = SAMPLE_RATE;
    const data = Buffer.alloc(sampleCount * 2);
    for (let index = 0; index < sampleCount; index += 1) {
      const envelope = Math.min(1, index / 400) * Math.min(1, (sampleCount - index) / 400);
      const sample = Math.round(2200 * envelope
        * Math.sin((2 * Math.PI * 440 * index) / SAMPLE_RATE));
      data.writeInt16LE(sample, index * 2);
    }
    const header = Buffer.alloc(44);
    header.write("RIFF", 0);
    header.writeUInt32LE(36 + data.length, 4);
    header.write("WAVE", 8);
    header.write("fmt ", 12);
    header.writeUInt32LE(16, 16);
    header.writeUInt16LE(1, 20);
    header.writeUInt16LE(1, 22);
    header.writeUInt32LE(SAMPLE_RATE, 24);
    header.writeUInt32LE(SAMPLE_RATE * 2, 28);
    header.writeUInt16LE(2, 32);
    header.writeUInt16LE(16, 34);
    header.write("data", 36);
    header.writeUInt32LE(data.length, 40);
    fs.writeFileSync(outputPath, Buffer.concat([header, data]));
  }
  return outputPath;
}

export function readWavDurationSeconds(filePath) {
  return readWavMetadata(filePath).durationSeconds;
}

export function readWavMetadata(filePath) {
  const buffer = fs.readFileSync(path.resolve(filePath));
  if (buffer.length < 12 || buffer.toString("ascii", 0, 4) !== "RIFF"
    || buffer.toString("ascii", 8, 12) !== "WAVE") {
    throw new Error("VOICE_TEST_AUDIO_FIXTURE is not a RIFF/WAVE file: " + filePath);
  }
  let offset = 12;
  let byteRate = 0;
  let dataSize = 0;
  let audioFormat = 0;
  let channels = 0;
  let sampleRate = 0;
  let bitsPerSample = 0;
  while (offset + 8 <= buffer.length) {
    const chunkSize = buffer.readUInt32LE(offset + 4);
    const chunkStart = offset + 8;
    const chunkEnd = chunkStart + chunkSize;
    if (chunkEnd > buffer.length) break;
    const chunkId = buffer.toString("ascii", offset, offset + 4);
    if (chunkId === "fmt " && chunkSize >= 12) {
      audioFormat = buffer.readUInt16LE(chunkStart);
      channels = buffer.readUInt16LE(chunkStart + 2);
      sampleRate = buffer.readUInt32LE(chunkStart + 4);
      byteRate = buffer.readUInt32LE(chunkStart + 8);
      if (chunkSize >= 16) bitsPerSample = buffer.readUInt16LE(chunkStart + 14);
    } else if (chunkId === "data") {
      dataSize = chunkSize;
    }
    // RIFF chunks are word aligned. Some speech synthesizers emit an
    // extended fmt chunk, so fixed offsets are not safe here.
    offset = chunkEnd + (chunkSize % 2);
  }
  if (!byteRate || !dataSize || !channels || !sampleRate) {
    throw new Error("VOICE_TEST_AUDIO_FIXTURE has no measurable PCM duration: " + filePath);
  }
  return {
    filePath: path.resolve(filePath),
    container: "RIFF/WAVE",
    audioFormat,
    channels,
    sampleRate,
    bitsPerSample,
    byteRate,
    dataBytes: dataSize,
    durationSeconds: dataSize / byteRate,
  };
}
