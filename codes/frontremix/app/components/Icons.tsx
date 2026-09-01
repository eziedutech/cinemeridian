/**
 * Category icons for the findings ribbons.
 *
 * Drawn inline rather than pulled from a set, for two reasons: the report is
 * meant to print to PDF, and an icon font or a remote sprite is the first
 * thing to go missing when a page is printed or opened offline. And a
 * continuity finding is about a specific physical thing - a shadow, a
 * waterline, a footprint - which a generic warning triangle does not say.
 *
 * All of them are stroke-only on a 24 unit grid, so they sit at any size and
 * take their colour from the ribbon.
 */

type IconProps = { size?: number };

const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
});

/** Sun: shadow direction, shadow length, anything solar. */
export function SunIcon({ size = 16 }: IconProps) {
  return (
    <svg {...base(size)} aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
    </svg>
  );
}

/** Thermometer: colour temperature drift. */
export function TemperatureIcon({ size = 16 }: IconProps) {
  return (
    <svg {...base(size)} aria-hidden="true">
      <path d="M14 14.8V4a2 2 0 1 0-4 0v10.8a4 4 0 1 0 4 0Z" />
      <path d="M12 9v6" />
    </svg>
  );
}

/** Footprints: anything that only accumulates. */
export function FootprintIcon({ size = 16 }: IconProps) {
  return (
    <svg {...base(size)} aria-hidden="true">
      <path d="M6 6.5c1.6 0 2.4 1.3 2.4 3S7.9 13 6.6 13 4 12.2 4 10.2 4.4 6.5 6 6.5Z" />
      <path d="M4.8 15.2c1.2-.5 2.7-.4 3.4.4.6.7.3 1.9-.9 2.2-1.2.3-2.6 0-3-.9-.3-.7 0-1.4.5-1.7Z" />
      <path d="M17.6 4c1.6 0 2 1.7 2 3.4s-1.1 2.7-2.4 2.7-2.4-1-2.4-3S16 4 17.6 4Z" />
      <path d="M15.4 12.4c1.2-.5 2.7-.4 3.4.4.6.7.3 1.9-.9 2.2-1.2.3-2.6 0-3-.9-.3-.7 0-1.4.5-1.7Z" />
    </svg>
  );
}

/** Waves: waterline, tide. */
export function TideIcon({ size = 16 }: IconProps) {
  return (
    <svg {...base(size)} aria-hidden="true">
      <path d="M2 8c2.2 0 2.2 2 4.4 2S8.6 8 10.8 8 13 10 15.2 10 17.4 8 19.6 8 21.8 10 24 10" />
      <path d="M2 13c2.2 0 2.2 2 4.4 2S8.6 13 10.8 13 13 15 15.2 15 17.4 13 19.6 13 21.8 15 24 15" />
      <path d="M2 18c2.2 0 2.2 2 4.4 2S8.6 18 10.8 18" />
    </svg>
  );
}

/** Wind: hair direction, breath, anything the air does. */
export function WindIcon({ size = 16 }: IconProps) {
  return (
    <svg {...base(size)} aria-hidden="true">
      <path d="M3 8h9.5a2.5 2.5 0 1 0-2.5-2.5" />
      <path d="M3 12h13a2.5 2.5 0 1 1-2.5 2.5" />
      <path d="M3 16h7.5a2 2 0 1 1-2 2" />
    </svg>
  );
}

/** Clock: a slate whose time does not match the sky. */
export function SlateIcon({ size = 16 }: IconProps) {
  return (
    <svg {...base(size)} aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5.2l3.4 2" />
    </svg>
  );
}

/** Layers: asset versions drifting apart between renders. */
export function AssetIcon({ size = 16 }: IconProps) {
  return (
    <svg {...base(size)} aria-hidden="true">
      <path d="M12 3 3 7.5l9 4.5 9-4.5L12 3Z" />
      <path d="M3 12.5 12 17l9-4.5" />
      <path d="M3 17 12 21.5 21 17" />
    </svg>
  );
}

/** Screen: the LED volume plate. */
export function VolumeIcon({ size = 16 }: IconProps) {
  return (
    <svg {...base(size)} aria-hidden="true">
      <rect x="2.5" y="4" width="19" height="12.5" rx="1.6" />
      <path d="M9 20h6M12 16.5V20" />
    </svg>
  );
}

/** Cloud: overcast, and anything that softens the light. */
export function CloudIcon({ size = 16 }: IconProps) {
  return (
    <svg {...base(size)} aria-hidden="true">
      <path d="M6.8 18.5a4.3 4.3 0 0 1-.4-8.6 5.6 5.6 0 0 1 10.8-1.3 3.9 3.9 0 0 1 .6 7.7 4 4 0 0 1-.6 0Z" />
    </svg>
  );
}

/**
 * Pick an icon for a finding.
 *
 * The entity is more specific than the finding type - a monotonic violation of
 * footprints and one of the waterline are different pictures - so the entity
 * decides where it can, and the type is the fallback.
 */
export function categoryIcon(
  findingType: string,
  entity: string,
  size = 16,
): { node: JSX.Element; label: string } {
  const byEntity: Record<string, { node: JSX.Element; label: string }> = {
    footprints: { node: <FootprintIcon size={size} />, label: "footprints" },
    waterline: { node: <TideIcon size={size} />, label: "tide" },
    primary_shadow: { node: <SunIcon size={size} />, label: "shadow" },
    sun: { node: <SunIcon size={size} />, label: "sun" },
    hair_a: { node: <WindIcon size={size} />, label: "wind" },
    breath_vapour: { node: <WindIcon size={size} />, label: "breath" },
    background_cloud: { node: <CloudIcon size={size} />, label: "cloud" },
  };
  if (byEntity[entity]) return byEntity[entity];

  const byType: Record<string, { node: JSX.Element; label: string }> = {
    slate_error: { node: <SlateIcon size={size} />, label: "slate" },
    asset_version_drift: { node: <AssetIcon size={size} />, label: "asset" },
    volume_plate_drift: { node: <VolumeIcon size={size} />, label: "LED volume" },
    physics_mismatch: { node: <SunIcon size={size} />, label: "light" },
    cross_take_drift: { node: <SunIcon size={size} />, label: "sun" },
    monotonic_violation: { node: <FootprintIcon size={size} />, label: "sequence" },
  };
  return byType[findingType] ?? { node: <TemperatureIcon size={size} />, label: "physical" };
}
