import type { PointerLine } from "../audio/playbackQueue";

export interface ActivePointer {
  key: number; // changes for every new target
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  lines?: PointerLine[];
  durationMs: number;
}

export type PState = "IDLE" | "PRESENTING" | "PAUSED" | "LISTENING" | "ANSWERING" | "END" | "OPEN_QA";

export interface ServerMsg {
  type: string;
  [k: string]: unknown;
}

export interface Snapshot {
  connected: boolean;
  ready: boolean;
  services: Record<string, string>;
  state: PState;
  reason: string;
  slideN: number;
  temporarySlide: boolean;
  caption: string;
  prevCaption: string;
  captionKind: "sentence" | "clip" | "answer" | "";
  transcript: string;
  bargeIn: boolean;
  sttStatus: string;
  warnings: string[];
  micOn: boolean;
  micError: string;
  hand: boolean;
  pointer: ActivePointer | null;
  notice: { key: number; text: string } | null;
  sessionId: string;
  startedAt: number | null;
  questions: { question: string; answer?: string; ts?: string }[];
  unanswered: { question: string; ts?: string }[];
  lastModel: string;
  lastFirstAudioMs: number | null;
  error: string;
}

export const initialSnapshot: Snapshot = {
  connected: false,
  ready: false,
  services: {},
  state: "IDLE",
  reason: "",
  slideN: 1,
  temporarySlide: false,
  caption: "",
  prevCaption: "",
  captionKind: "",
  transcript: "",
  bargeIn: true,
  sttStatus: "unknown",
  warnings: [],
  micOn: false,
  micError: "",
  hand: false,
  pointer: null,
  notice: null,
  sessionId: "",
  startedAt: null,
  questions: [],
  unanswered: [],
  lastModel: "",
  lastFirstAudioMs: null,
  error: "",
};
