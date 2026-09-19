export interface NormalizedLandmark {
  x: number;
  y: number;
  z: number;
  visibility?: number;
}

export interface PoseResults {
  poseLandmarks?: NormalizedLandmark[];
  poseWorldLandmarks?: NormalizedLandmark[];
  image?: CanvasImageSource;
}

export interface PoseConfig {
  locateFile?: (file: string) => string;
  modelComplexity?: 0 | 1 | 2;
  smoothLandmarks?: boolean;
  minDetectionConfidence?: number;
  minTrackingConfidence?: number;
}

export interface Pose {
  setOptions(options: PoseConfig): void;
  onResults(callback: (results: PoseResults) => void): void;
  send(input: { image: CanvasImageSource }): Promise<void>;
  close(): Promise<void>;
}

export interface PoseConstructor {
  new (config?: PoseConfig): Pose;
}

export interface CameraOptions {
  onFrame: () => Promise<void> | void;
  width?: number;
  height?: number;
}

export interface MediaPipeCamera {
  start(): Promise<void>;
  stop(): void;
}

export interface CameraConstructor {
  new (video: HTMLVideoElement, options: CameraOptions): MediaPipeCamera;
}

declare global {
  interface Window {
    Pose?: PoseConstructor;
    Camera?: CameraConstructor;
  }
}
