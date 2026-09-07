import { useCallback, useEffect, useRef, useState } from "react";
import { AudioPlayer, AudioStatus, createAudioPlayer, setAudioModeAsync } from "expo-audio";

type PlayerState = {
  isLoaded: boolean;
  isPlaying: boolean;
  positionMillis: number;
  durationMillis: number;
  error: string | null;
};

const INITIAL: PlayerState = {
  isLoaded: false,
  isPlaying: false,
  positionMillis: 0,
  durationMillis: 0,
  error: null,
};

// Wraps expo-audio's imperative AudioPlayer (expo-av was removed in SDK 54).
// External contract is unchanged from the old expo-av version — callers still
// get milliseconds and load / play / pause / seek — so screens didn't change.
// expo-audio itself works in seconds; conversion happens here.
export function useAudioPlayer(onFinish?: () => void) {
  const playerRef = useRef<AudioPlayer | null>(null);
  const subRef = useRef<{ remove: () => void } | null>(null);
  const finishRef = useRef(onFinish);
  finishRef.current = onFinish;

  const [state, setState] = useState<PlayerState>(INITIAL);

  useEffect(() => {
    setAudioModeAsync({ playsInSilentMode: true, shouldPlayInBackground: false }).catch(() => {
      /* non-fatal */
    });
    return () => {
      subRef.current?.remove();
      playerRef.current?.remove();
      playerRef.current = null;
    };
  }, []);

  const onStatus = useCallback((status: AudioStatus) => {
    setState({
      isLoaded: status.isLoaded,
      isPlaying: status.playing,
      positionMillis: Math.round((status.currentTime ?? 0) * 1000),
      durationMillis: Math.round((status.duration ?? 0) * 1000),
      error: status.error ?? null,
    });
    if (status.didJustFinish) {
      finishRef.current?.();
    }
  }, []);

  const load = useCallback(
    async (uri: string, autoPlay: boolean) => {
      setState({ ...INITIAL });
      try {
        subRef.current?.remove();
        subRef.current = null;
        playerRef.current?.remove();
        playerRef.current = null;
        if (!uri) return;

        const player = createAudioPlayer({ uri }, { updateInterval: 250 });
        playerRef.current = player;
        subRef.current = player.addListener("playbackStatusUpdate", onStatus);
        if (autoPlay) player.play();
      } catch (err) {
        setState((prev) => ({
          ...prev,
          error: err instanceof Error ? err.message : "Couldn't load this track.",
        }));
      }
    },
    [onStatus]
  );

  const play = useCallback(async () => {
    playerRef.current?.play();
  }, []);

  const pause = useCallback(async () => {
    playerRef.current?.pause();
  }, []);

  const seek = useCallback(async (millis: number) => {
    await playerRef.current?.seekTo(Math.max(0, millis) / 1000);
  }, []);

  return { ...state, load, play, pause, seek };
}
