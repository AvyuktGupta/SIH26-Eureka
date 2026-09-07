import { MapContainer, TileLayer, Polyline, Polygon, CircleMarker, Pane } from "react-leaflet";

function riskColor(score, state) {
  if (state === "pending") return "#e6c35c";
  if (score < 0.25) return "#3d9a64";
  if (score < 0.45) return "#b7c75a";
  if (score < 0.62) return "#e0a045";
  return "#d45b4a";
}

export default function MapView({ overlay, route, alternate, vehicle, origin, dest }) {
  const routeLine = (route?.geometry?.coordinates || []).map(([lon, lat]) => [lat, lon]);
  const altLine = (alternate?.geometry?.coordinates || []).map(([lon, lat]) => [lat, lon]);

  return (
    <div className="map-wrap">
    <MapContainer
      center={[32.11, 77.15]}
      zoom={11}
      className="map"
      zoomControl={true}
    >
      <TileLayer
        attribution='&copy; OpenStreetMap'
        url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
      />
      <Pane name="h3pane" style={{ zIndex: 350 }}>
        {(overlay || []).map((cell) => (
          <Polygon
            key={cell.h3_index}
            positions={(cell.boundary || []).map(([lon, lat]) => [lat, lon])}
            pathOptions={{
              color: riskColor(cell.fused_score, cell.hysteresis_state),
              weight: cell.on_active_route ? 1.4 : 0.4,
              fillColor: riskColor(cell.fused_score, cell.hysteresis_state),
              fillOpacity: cell.on_active_route ? 0.38 : 0.16,
            }}
          />
        ))}
      </Pane>
      {routeLine.length > 1 && (
        <Polyline positions={routeLine} pathOptions={{ color: "#7eb6ff", weight: 5, opacity: 0.95 }} />
      )}
      {altLine.length > 1 && (
        <Polyline
          positions={altLine}
          pathOptions={{ color: "#ffb25b", weight: 5, opacity: 0.9, dashArray: "10 8" }}
        />
      )}
      {origin && <CircleMarker center={[origin.lat, origin.lon]} radius={6} pathOptions={{ color: "#9ad0ff", fillOpacity: 1 }} />}
      {dest && <CircleMarker center={[dest.lat, dest.lon]} radius={6} pathOptions={{ color: "#ffd48a", fillOpacity: 1 }} />}
      {vehicle && (
        <CircleMarker
          center={[vehicle.lat, vehicle.lon]}
          radius={9}
          pathOptions={{ color: "#fff", fillColor: "#5b8cff", fillOpacity: 1, weight: 2 }}
        />
      )}
    </MapContainer>
    </div>
  );
}
