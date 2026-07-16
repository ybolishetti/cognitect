import { ROOM_COLORS } from "@/lib/constants";
import type { Layout, LayoutOpening, LayoutRoom, LayoutWall } from "@/lib/api";

type LayoutPreviewProps = {
  layout: Layout;
  className?: string;
  showRoomLabels?: boolean;
};

function centroid(room: LayoutRoom): { x: number; y: number } {
  // vertices form a closed polygon (first === last) — drop the duplicate
  // closing point so it isn't double-counted in the average.
  const pts = room.vertices.slice(0, -1);
  const sum = pts.reduce((acc, [x, y]) => ({ x: acc.x + x, y: acc.y + y }), { x: 0, y: 0 });
  return { x: sum.x / pts.length, y: sum.y / pts.length };
}

export function LayoutPreview({ layout, className, showRoomLabels = true }: LayoutPreviewProps) {
  const wallsById = new Map<string, LayoutWall>(layout.walls.map((w) => [w.id, w]));
  const fontSize = Math.max(0.8, Math.min(2.0, layout.extent_x_ft / 40));

  return (
    <svg
      viewBox={`0 0 ${layout.extent_x_ft} ${layout.extent_y_ft}`}
      preserveAspectRatio="xMidYMid meet"
      className={className}
      role="img"
      aria-label="Floor plan preview"
    >
      {/* Layout uses math coords (y-up); SVG uses screen coords (y-down).
          Flipping here means room/wall/opening children can use raw vertex
          coordinates without any per-point transformation. */}
      <g transform={`scale(1 -1) translate(0 -${layout.extent_y_ft})`}>
        {layout.rooms.map((room) => (
          <polygon
            key={room.id}
            points={room.vertices.map(([x, y]) => `${x},${y}`).join(" ")}
            fill={ROOM_COLORS[room.room_type] ?? ROOM_COLORS.other}
            fillOpacity={0.4}
            stroke="none"
          />
        ))}
        {layout.walls.map((wall) => (
          <line
            key={wall.id}
            x1={wall.start[0]}
            y1={wall.start[1]}
            x2={wall.end[0]}
            y2={wall.end[1]}
            stroke="#1e293b"
            strokeWidth={wall.thickness_ft ?? 0.5}
            strokeLinecap="butt"
          />
        ))}
        {layout.openings.map((opening) => {
          const wall = wallsById.get(opening.wall_id);
          if (!wall) return null;
          const cutout = openingCutout(wall, opening);
          if (!cutout) return null;
          return (
            <line
              key={opening.id}
              x1={cutout.x1}
              y1={cutout.y1}
              x2={cutout.x2}
              y2={cutout.y2}
              stroke="#ffffff"
              strokeWidth={(wall.thickness_ft ?? 0.5) + 0.05}
              strokeLinecap="butt"
            />
          );
        })}
      </g>
      {/* Labels render in a separate, un-flipped group — under the y-flip
          above, text would render upside down. Coordinates are converted to
          screen space by hand instead. */}
      {showRoomLabels && (
        <g fontSize={fontSize} textAnchor="middle" fill="#1e293b">
          {layout.rooms.map((room) => {
            const c = centroid(room);
            return (
              <text key={room.id} x={c.x} y={layout.extent_y_ft - c.y} dominantBaseline="middle">
                {room.name}
              </text>
            );
          })}
        </g>
      )}
    </svg>
  );
}

function openingCutout(
  wall: LayoutWall,
  opening: LayoutOpening
): { x1: number; y1: number; x2: number; y2: number } | null {
  const [sx, sy] = wall.start;
  const [ex, ey] = wall.end;
  const dx = ex - sx;
  const dy = ey - sy;
  const length = Math.sqrt(dx * dx + dy * dy);
  if (length === 0) return null;
  const ux = dx / length;
  const uy = dy / length;
  const from = opening.offset_ft;
  const to = opening.offset_ft + opening.width_ft;
  return {
    x1: sx + ux * from,
    y1: sy + uy * from,
    x2: sx + ux * to,
    y2: sy + uy * to,
  };
}
