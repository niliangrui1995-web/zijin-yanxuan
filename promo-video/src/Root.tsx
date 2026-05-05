import "./index.css";
import { Composition } from "remotion";
import { ZIJIN_PROMO_DURATION, ZijinPromo } from "./Composition";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="ZijinPromo"
        component={ZijinPromo}
        durationInFrames={ZIJIN_PROMO_DURATION}
        fps={30}
        width={1920}
        height={1080}
      />
    </>
  );
};
