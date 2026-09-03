/**
 * Pull the head and the tail out of a clip, in the browser.
 *
 * The video never leaves the machine. A `<video>` element decodes it, a canvas
 * copies single frames out, and only those few JPEGs are ever sent anywhere.
 * That is why a hundred megabyte file is not a hundred megabyte upload, and it
 * is also the honest answer to "where does my footage go" - nowhere.
 *
 * Which frames are taken is the part that matters and the part that differs
 * from every general video-to-image tool. Those pick the sharpest frame, or
 * every nth. A cut joins the *last* moment of one shot to the *first* moment
 * of the next, so those two moments are what a continuity check needs, sharp
 * or not. Everything here is arranged around getting exactly those.
 */

export type ExtractLimits = {
  maxBytes: number;
  maxSeconds: number;
};

export const DEFAULT_LIMITS: ExtractLimits = {
  maxBytes: 100 * 1024 * 1024,
  maxSeconds: 120,
};

export type ExtractedFrame = {
  role: "head" | "tail";
  /** Seconds from the start of the clip. */
  at: number;
  blob: Blob;
  dataUrl: string;
};

export type ExtractResult = {
  frames: ExtractedFrame[];
  durationSeconds: number;
  width: number;
  height: number;
};

/** Longest edge of an extracted frame. Vision does not need more, and every
 *  pixel past this is upload time for nothing. */
const MAX_EDGE = 1280;
const JPEG_QUALITY = 0.88;

/** A seek to exactly `duration` lands past the last frame on most decoders. */
const TAIL_MARGIN_SECONDS = 0.06;

/** How long a media event is given before the wait is called a failure. Both
 *  are generous: a large clip on a slow machine is still opening at ten
 *  seconds, and a wrong answer here is a clip refused that would have worked. */
const METADATA_TIMEOUT_MS = 30_000;
const SEEK_TIMEOUT_MS = 20_000;

export class ClipTooLarge extends Error {}
export class ClipTooLong extends Error {}
/** The browser opened the file and then stopped, or never opened it at all. */
export class ClipUnreadable extends Error {}

export async function extractHeadAndTail(
  file: File,
  {
    framesPerEnd = 3,
    spacingSeconds = 0.5,
    limits = DEFAULT_LIMITS,
    onProgress,
  }: {
    framesPerEnd?: number;
    spacingSeconds?: number;
    limits?: ExtractLimits;
    onProgress?: (done: number, total: number) => void;
  } = {},
): Promise<ExtractResult> {
  if (file.size > limits.maxBytes) {
    throw new ClipTooLarge(
      `${(file.size / 1024 / 1024).toFixed(0)} MB is over the ${(
        limits.maxBytes /
        1024 /
        1024
      ).toFixed(0)} MB limit.`,
    );
  }

  // Chrome does not decode video in a tab nobody is looking at, and a element
  // that never fires `loadedmetadata` leaves every wait below hanging with
  // nothing to report. So the work waits for the tab rather than starting into
  // a decoder that has been put to sleep, and picks up by itself the moment
  // somebody comes back to it.
  await whenVisible();

  const url = URL.createObjectURL(file);
  const video = document.createElement("video");
  video.preload = "auto";
  video.muted = true;
  video.playsInline = true;
  video.src = url;

  try {
    await once(video, "loadedmetadata");
    const duration = video.duration;
    if (!Number.isFinite(duration) || duration <= 0) {
      throw new Error("This file has no readable duration.");
    }
    if (duration > limits.maxSeconds) {
      throw new ClipTooLong(
        `${duration.toFixed(0)} seconds is over the ${limits.maxSeconds} second limit.`,
      );
    }

    const scale = Math.min(1, MAX_EDGE / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    const context = canvas.getContext("2d");
    if (!context) throw new Error("This browser will not give us a canvas to draw on.");

    const times = planTimes(duration, framesPerEnd, spacingSeconds);
    const frames: ExtractedFrame[] = [];

    for (const [index, plan] of times.entries()) {
      await seekTo(video, plan.at);
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await toBlob(canvas);
      frames.push({
        role: plan.role,
        at: plan.at,
        blob,
        dataUrl: canvas.toDataURL("image/jpeg", 0.6),
      });
      onProgress?.(index + 1, times.length);
    }

    return {
      frames,
      durationSeconds: duration,
      width: canvas.width,
      height: canvas.height,
    };
  } finally {
    video.removeAttribute("src");
    video.load();
    URL.revokeObjectURL(url);
  }
}

/**
 * Which moments to grab.
 *
 * The first head frame is the clip's very first, and the last tail frame is
 * its very last: those are the two that would touch the neighbouring shots.
 * The others sit just inside, so a person can see whether the end frames are
 * representative or happened to catch something odd.
 */
export function planTimes(
  duration: number,
  framesPerEnd: number,
  spacing: number,
): Array<{ role: "head" | "tail"; at: number }> {
  const last = Math.max(duration - TAIL_MARGIN_SECONDS, 0);
  const usableSpacing = Math.min(spacing, last / Math.max(framesPerEnd * 2, 1));

  const head = Array.from({ length: framesPerEnd }, (_, index) => ({
    role: "head" as const,
    at: Math.min(index * usableSpacing, last),
  }));
  const tail = Array.from({ length: framesPerEnd }, (_, index) => ({
    role: "tail" as const,
    at: Math.max(last - (framesPerEnd - 1 - index) * usableSpacing, 0),
  }));

  return [...head, ...tail];
}

function seekTo(video: HTMLVideoElement, time: number): Promise<void> {
  return deadline(
    new Promise<void>((resolve, reject) => {
      const onSeeked = () => {
        cleanup();
        resolve();
      };
      const onError = () => {
        cleanup();
        reject(new Error(`Could not seek to ${time.toFixed(2)}s in this file.`));
      };
      const cleanup = () => {
        video.removeEventListener("seeked", onSeeked);
        video.removeEventListener("error", onError);
      };
      video.addEventListener("seeked", onSeeked, { once: true });
      video.addEventListener("error", onError, { once: true });
      video.currentTime = time;
    }),
    SEEK_TIMEOUT_MS,
    `The browser stopped part way through this clip, at ${time.toFixed(2)}s.`,
  );
}

/**
 * Resolve once the tab is on screen.
 *
 * Not a guard against a rare case: switching tabs while several clips decode is
 * the ordinary thing to do, and a decoder that has been suspended fires no
 * event at all, so every wait downstream would sit there for as long as the
 * page is left open.
 */
function whenVisible(): Promise<void> {
  if (document.visibilityState !== "hidden") return Promise.resolve();
  return new Promise((resolve) => {
    const onChange = () => {
      if (document.visibilityState === "hidden") return;
      document.removeEventListener("visibilitychange", onChange);
      resolve();
    };
    document.addEventListener("visibilitychange", onChange);
  });
}

/**
 * Fail loudly rather than wait forever.
 *
 * Every wait here is on a media event, and a decoder that gives up gives no
 * sign of it. Without a deadline the page keeps saying it is working, which is
 * worse than saying it failed.
 */
function deadline<T>(work: Promise<T>, ms: number, message: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout>;
  const expiry = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new ClipUnreadable(message)), ms);
  });
  return Promise.race([work, expiry]).finally(() => clearTimeout(timer)) as Promise<T>;
}

function toBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("Could not encode a frame."))),
      "image/jpeg",
      JPEG_QUALITY,
    );
  });
}

function once(target: HTMLVideoElement, event: string): Promise<void> {
  return deadline(
    new Promise<void>((resolve, reject) => {
      target.addEventListener(event, () => resolve(), { once: true });
      target.addEventListener(
        "error",
        () => reject(new Error("This file could not be decoded in the browser.")),
        { once: true },
      );
    }),
    METADATA_TIMEOUT_MS,
    "This browser did not open the clip. It may be a format it cannot decode.",
  );
}
