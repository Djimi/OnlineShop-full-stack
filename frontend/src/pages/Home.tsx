export default function Home() {
  return (
    <>
      {/* HERO */}
      <section className="max-w-[1320px] mx-auto px-6 md:px-16 pt-24 pb-28 text-center">
        <h1 className="font-display font-light text-[clamp(64px,8vw,128px)] leading-[1] tracking-[-0.015em] max-w-5xl mx-auto">
          A small shop
          <br />
          of <em className="italic">quiet</em>,{' '}
          <span className="italic text-[#7a3b2c] font-normal">well-made</span>
          <br />
          things.
        </h1>

        <p className="mt-10 text-[#5b524a] max-w-xl mx-auto text-base md:text-[17px] leading-[1.7] font-light">
          We curate fewer items, more carefully — for kitchens, desks, and
          rooms that deserve to feel calm.
        </p>
      </section>

      {/* FEATURED PRODUCT STRIP */}
      <section className="max-w-[1320px] mx-auto px-6 md:px-16 pb-28">
        <div className="relative aspect-[21/9] overflow-hidden bg-gradient-to-b from-[#e8e1d3] to-[#dcd2bf]">
          <div className="absolute top-2 right-14 md:top-0 md:right-0 md:bottom-0 md:left-0 md:grid md:place-items-center">
            <svg viewBox="0 0 240 240" fill="none" className="w-16 md:w-[30%] max-w-[260px]">
              <ellipse cx="120" cy="226" rx="80" ry="5" fill="#1f1a14" opacity=".15" />
              <path
                d="M75 80 Q75 60 100 60 L100 50 L140 50 L140 60 Q165 60 165 80 L172 210 Q120 230 68 210 Z"
                fill="#f4f1ea"
                stroke="#1f1a14"
                strokeWidth="1.2"
              />
              <ellipse cx="120" cy="80" rx="42" ry="6" fill="#1f1a14" opacity=".15" />
              <line x1="100" y1="140" x2="140" y2="140" stroke="#1f1a14" strokeWidth=".8" opacity=".4" />
              <path
                d="M180 130 Q180 120 195 120 L205 120 Q220 120 220 130 L222 210 Q200 222 178 210 Z"
                fill="#7a3b2c"
                opacity=".85"
              />
            </svg>
          </div>

          <div className="absolute bottom-8 left-9">
              <span className="block font-display italic text-[#1f1a14] text-xl md:text-2xl">
              No. 04 — The Maren Carafe
            </span>
            <span className="block text-[11px] tracking-[0.18em] uppercase text-[#5b524a] mt-1.5">
              Hand-blown glass · 1L
            </span>
          </div>
          <div className="absolute bottom-8 right-6 md:right-9 font-display italic text-[#7a3b2c] text-2xl md:text-[28px]">
            €48
          </div>
        </div>
      </section>

      {/* THREE THINGS WE BELIEVE */}
      <section className="border-t border-[#dcd5c7] max-w-[1320px] mx-auto px-6 md:px-16 py-24">
        <div className="text-center mb-16">
          <h2 className="font-display font-light text-5xl leading-[1.05]">
            Three things we <em className="italic text-[#7a3b2c] font-normal">believe</em>.
          </h2>
          <p className="mt-4 text-[#5b524a] text-sm font-light max-w-lg mx-auto">
            Our shop is small on purpose. These three ideas guide every item we add to it.
          </p>
        </div>

        <div className="grid md:grid-cols-3 gap-12 md:gap-16">
          <Belief
            numeral="i."
            title="Less, but better"
            text="We add one or two pieces a season — not a hundred. Every object earns its shelf."
          />
          <Belief
            numeral="ii."
            title="Honest provenance"
            text="We name every maker. Each product page tells you where it comes from and how it's made."
          />
          <Belief
            numeral="iii."
            title="Quietly priced"
            text="No fake discounts, no flash sales. The price you see is the price we believe is fair."
          />
        </div>
      </section>

      <footer className="border-t border-[#dcd5c7] py-10 text-center text-[11px] tracking-[0.18em] uppercase text-[#5b524a]">
        OnlineShop · Made in small batches · MMXXVI
      </footer>
    </>
  );
}

interface BeliefProps {
  numeral: string;
  title: string;
  text: string;
}

function Belief({ numeral, title, text }: BeliefProps) {
  return (
    <div className="text-center">
      <div className="font-display italic text-5xl text-[#7a3b2c] font-light leading-none mb-6">
        {numeral}
      </div>
      <h3 className="font-display font-normal text-2xl tracking-[0.005em] mb-3.5">{title}</h3>
      <p className="text-[#5b524a] text-sm leading-[1.7] font-light max-w-[280px] mx-auto">
        {text}
      </p>
    </div>
  );
}
