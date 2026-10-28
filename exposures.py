"""Link environmental exposures to residential locations, driven by config/exposures.yaml."""
import shutil
import geopandas as gpd
import pandas as pd
import yaml
from shapely.ops import unary_union


def load_layer(path, crs):
    """Copy a GeoPackage from Drive to local disk, read it, reproject and repair geometries."""
    local = "/content/_layer.gpkg"
    shutil.copy(path, local)
    gdf = gpd.read_file(local).to_crs(crs)
    gdf["geometry"] = gdf.geometry.make_valid()
    return gdf


def merged_polygons(layer, simplify_m=1):
    """Merge overlapping features and keep clean polygons only (avoids double counting)."""
    merged = unary_union(layer.geometry.values)
    parts = gpd.GeoDataFrame(geometry=[merged], crs=layer.crs).explode(index_parts=False)
    parts = parts[parts.geom_type.isin(["Polygon", "MultiPolygon"])]
    parts["geometry"] = parts.geometry.simplify(simplify_m)
    return parts[~parts.is_empty & parts.is_valid].reset_index(drop=True)


def area_share(homes, layer, buffers_m):
    """Share of each circular buffer covered by the layer's polygons."""
    parts = merged_polygons(layer)
    results = {}
    for b in buffers_m:
        buf = gpd.GeoDataFrame({"home_id": homes["home_id"]}, geometry=homes.buffer(b), crs=homes.crs)
        overlap = gpd.overlay(buf, parts, how="intersection")
        covered = overlap.assign(a=overlap.area).groupby("home_id")["a"].sum()
        results[b] = (homes["home_id"].map(covered).fillna(0) / buf.area.values).round(3).values
    return results


def distance(homes, layer):
    """Distance from each home to the nearest feature of the layer (0 if inside)."""
    merged = unary_union(layer.geometry.values)
    return homes.distance(merged).round(1).values


def run_pipeline(config_path, homes, base_dir="."):
    """Compute all exposures listed in the config. Returns the exposure table and a data dictionary."""
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    homes = homes.to_crs(cfg["crs"])
    table = homes[["home_id"]].copy()
    dictionary = []

    for ex in cfg["exposures"]:
        layer = load_layer(f"{base_dir}/{ex['source_file']}", cfg["crs"])
        entry = {
            "description": ex["description"], "method": ex["method"], "unit": ex["unit"],
            "source": ex["source"], "source_file": ex["source_file"],
            "valid_min": ex["valid_range"][0], "valid_max": ex["valid_range"][1],
        }
        if ex["method"] == "area_share":
            for b, values in area_share(homes, layer, ex["buffers_m"]).items():
                col = f"{ex['name']}_{b}m"
                table[col] = values
                dictionary.append({"variable": col, "buffer_m": b, **entry})
        elif ex["method"] == "distance":
            table[ex["name"]] = distance(homes, layer)
            dictionary.append({"variable": ex["name"], "buffer_m": None, **entry})
        else:
            raise ValueError(f"Unknown method: {ex['method']}")
        print(f"Done: {ex['name']}")

    return table, pd.DataFrame(dictionary)
