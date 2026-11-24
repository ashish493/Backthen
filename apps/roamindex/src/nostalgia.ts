import { useEffect, useState } from "react";

/** Status words shown while the app works: a little travel nostalgia instead of "Loading…". */
export const NOSTALGIA = [
  "Reminiscing", "Wandering back", "Retracing footsteps", "Unpacking suitcases", "Dusting off albums",
  "Rewinding the road trip", "Chasing old sunsets", "Collecting postcards", "Revisiting old haunts",
  "Flipping through passports", "Daydreaming", "Reliving golden hours", "Following old trails", "Rediscovering",
  "Sorting souvenirs", "Tracing coastlines", "Stamping passports", "Wandering old streets", "Savouring the view",
  "Catching the last train", "Gathering moments", "Humming that road-trip song", "Finding the way back",
  "Reading old boarding passes", "Smelling the sea air again", "Counting the stars from that night",
];

function shuffled<T>(xs: T[]) {
  const a = [...xs];
  for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
  return a;
}

/** The words in a random order, fixed for the component's lifetime: `wordAt(step)` for step 0, 1, 2… */
export function useWordOrder(words: string[] = NOSTALGIA) {
  const [order] = useState(() => shuffled(words));
  return (step: number) => order[step % order.length];
}

/** Cycles through `words` in a random order, one every `ms`; `tick` changes on every step (use it as a React key to fade).
 *  Pass `ms = 0` to stop the timer (the caller drives the steps itself). */
export function useRotating(words: string[] = NOSTALGIA, ms = 2800) {
  const wordAt = useWordOrder(words);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!ms) return;
    const id = setInterval(() => setTick((t) => t + 1), ms);
    return () => clearInterval(id);
  }, [ms]);
  return { word: wordAt(tick), tick, wordAt };
}
