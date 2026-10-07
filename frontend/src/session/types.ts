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
  micOn: boolean;
  micError: string;
  hand: boolean;
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
  micOn: false,
  micError: "",
  hand: false,
  questions: [],
  unanswered: [],
  lastModel: "",
  lastFirstAudioMs: null,
  error: "",
};
