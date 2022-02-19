import csv
import json
from datetime import datetime
import re
import os
import shutil
from typing import List, Optional, TypedDict

import pystac
import pystac.extensions.scientific
import pystac.summaries
import pystac.layout
import click
from dateutil.parser import parse
from pygeoif import geometry
from slugify import slugify


RE_ID_REP = re.compile('r[^A-Za-z0-9\- ]+')



class MultiCollectionItem(pystac.Item):
    def set_collection(self, collection: Optional[pystac.Collection]) -> "Item":

        # self.remove_links(pystac.RelType.COLLECTION)
        self.collection_id = None
        if collection is not None:
            self.add_link(pystac.Link.collection(collection))
            self.collection_id = collection.id

        return self


def get_depth(maybe_list):
    if isinstance(maybe_list, (list, tuple)):
        return get_depth(maybe_list[0]) + 1
    return 0


def get_themes(obj):
    return [
        obj[f"Theme{i}"]
        for i in range(1, 7)
        if obj[f"Theme{i}"]
    ]


def parse_date(source):
    if not source:
        return None
    year, month = source.split(".")
    return datetime(int(year), int(month) + 1, 1)


def get_geometry(source):
    geom = None
    if not source:
        pass
    elif source.startswith("Multipolygon"):
        # geom = geometry.from_wkt(source.replace("Multipolygon", "MULTIPOLYGON"))
        # TODO: figure out a way to parse this
        pass
    else:
        try:
            raw_geom = json.loads(source)
        except ValueError:
            print(source)
            return None
        depth = get_depth(raw_geom)
        if depth == 1:
            geom = geometry.Point(*raw_geom)
        elif depth == 3:
            shell, *holes = raw_geom
            geom = geometry.Polygon(shell, holes or None)

    if geom:
        return geom.__geo_interface__
    return None


def product_to_item(obj):
    properties = {
        "start_datetime": obj["Start"] and parse_date(obj["Start"]).isoformat() or None,
        "end_datetime": obj["End"] and parse_date(obj["End"]).isoformat() or None,
        "title": obj["Product"],
        "description": obj["Description"],
        "mission": obj["EO_Missions"],
        "osc:project": obj["Project"],
        "osc:themes": get_themes(obj),
        "osc:variable": obj["Variable"],
        "osc:status": obj["Status"],  # TODO maybe use a STAC field
        "osc:region": obj["Region"],
        "osc:type": "Product",
        # scientific extension DOI
    }
    item = pystac.Item(
        f"product-{obj['ID']}",
        get_geometry(obj["Polygon"]),
        None,
        obj["Start"] and parse_date(obj["Start"]) or None,
        properties=properties,
        href=f"products/product-{obj['ID']}.json"
    )
    item.add_link(
        pystac.Link(
            pystac.RelType.VIA,
            obj["Website"]
        )
    )
    item.add_link(
        pystac.Link(
            pystac.RelType.VIA,
            obj["Access"]
        )
    )
    item.add_link(
        pystac.Link(
            pystac.RelType.VIA,
            obj["Documentation"]
        )
    )

    sci_ext = pystac.extensions.scientific.ScientificExtension.ext(item, True)
    sci_ext.apply(obj["DOI"])
    return item


def project_to_item(obj):
    properties = {
        "start_datetime": parse(obj["Start_Date_Project"]).isoformat(),
        "end_datetime": parse(obj["End_Date_Project"]).isoformat(),
        "title": obj["Project_Name"],
        "description": obj["Short_Description"],
        "osc:themes": get_themes(obj),
        "osc:status": obj["Status"],  # TODO maybe use a STAC field
        "osc:consortium": obj["Consortium"],
        "osc:technical_officer": {
            "name": obj["TO"],
            "email": obj["TO_E-mail"],
        },
        "osc:type": "Project",
    }
    item = MultiCollectionItem(
        f"project-{obj['Project_ID']}",
        None,
        None,
        parse(obj["Start_Date_Project"]),
        properties=properties,
        href=f"projects/project-{obj['Project_ID']}.json"
    )
    item.add_link(
        pystac.Link(
            pystac.RelType.VIA,
            obj["Website"]
        )
    )
    item.add_link(
        pystac.Link(
            pystac.RelType.VIA,
            obj["Eo4Society_link"]
        )
    )
    return item


def theme_to_collection(obj):
    identifier = obj["theme"].strip()
    collection = pystac.Collection(
        identifier,
        obj["description"],
        extent=pystac.Extent(
            pystac.SpatialExtent([-180, -90, 180, 90]),
            pystac.TemporalExtent([[None, None]])
        ),
        href=f"themes/{identifier}.json"
    )
    collection.extra_fields = {
        "osc:type": "Theme",
    }
    collection.add_link(
        pystac.Link(
            pystac.RelType.VIA,
            obj["link"]
        )
    )
    return collection


def variable_to_collection(obj):
    identifier = obj["variable"].strip()
    collection = pystac.Collection(
        identifier,
        obj["variable description"],
        extent=pystac.Extent(
            pystac.SpatialExtent([-180, -90, 180, 90]),
            pystac.TemporalExtent([[None, None]])
        ),
        href=f"variables/{identifier}.json"
    )
    collection.extra_fields = {
        "osc:theme": obj["theme"],
        "osc:type": "Variable",
    }
    collection.add_link(
        pystac.Link(
            pystac.RelType.VIA,
            obj["link"]
        )
    )
    return collection


@click.command()
@click.argument('variables_file', type=click.File('r'))
@click.argument('themes_file', type=click.File('r'))
@click.argument('projects_file', type=click.File('r'))
@click.argument('products_file', type=click.File('r'))
@click.option("--out-dir", "-o", default="dist", type=str)
def main(variables_file, themes_file, projects_file, products_file, out_dir):
    # with open("Variables.csv") as f:
    variables = list(csv.DictReader(variables_file))

    # with open("Themes.csv") as f:
    themes = list(csv.DictReader(themes_file))

    # with open("Projects-2021-12-20.csv") as f:
    projects = list(csv.DictReader(projects_file))

    # with open("Products-2021-12-20.csv") as f:
    products = list(csv.DictReader(products_file))

    catalog = pystac.Catalog(
        'OSC-Catalog',
        'OSC-Catalog',
        href="catalog.json"
    )

    theme_collections = [
        theme_to_collection(theme)
        for theme in themes
    ]

    theme_map = {
        slugify(coll.id): coll
        for coll in theme_collections
    }

    variable_collections = [
        variable_to_collection(variable)
        for variable in variables
    ]

    variable_map = {
        slugify(coll.id): coll
        for coll in variable_collections
    }

    product_items = [
        product_to_item(product)
        for product in products
    ]

    # TODO: figure out what to do with projects
    project_items = [
        project_to_item(project)
        for project in projects
    ]

    # place variable collections into theme collections
    for coll in variable_collections:
        theme_coll = theme_map[slugify(coll.extra_fields["osc:theme"])]
        theme_coll.add_child(coll)

    # put products into variable collections
    for item in product_items:
        try:
            variable_coll = variable_map[slugify(item.properties["osc:variable"])]
        except KeyError:
            print(f"Missing variable {item.properties['osc:variable']}")
        variable_coll.add_item(item)

    # put projects into their respective theme collections
    for item in project_items:
        for theme in item.properties["osc:themes"]:
            theme_map[slugify(theme)].add_item(item)

    catalog.add_children(theme_collections)
    # catalog.add_items(project_items)

    # calculate summary information for variable and themes
    for coll in variable_collections:
        years = set()
        i = 0
        for i, item in enumerate(coll.get_items(), start=1):
            if item.properties["start_datetime"]:
                years.add(
                    parse(item.properties["start_datetime"]).year
                    # int(item.properties["start_datetime"].split(".")[0])
                )
        # TODO: use summaries instead?
        coll.extra_fields["osc:years"] = sorted(years)
        coll.extra_fields["osc:numberOfProducts"] = i

    for coll in theme_collections:
        years = set()
        number_of_products = 0
        i = 0
        for i, sub_coll in enumerate(coll.get_collections(), start=1):
            years.update(sub_coll.extra_fields["osc:years"])
            number_of_products += sub_coll.extra_fields["osc:numberOfProducts"]

        coll.extra_fields["osc:years"] = sorted(years)
        coll.extra_fields["osc:numberOfProducts"] = number_of_products
        coll.extra_fields["osc:numberOfVariables"] = i

        for i, item in enumerate(coll.get_items(), start=1):
            pass

        coll.extra_fields["osc:numberOfProjects"] = i

    years = set()
    number_of_products = 0
    number_of_variables = 0
    i = 0
    for i, coll in enumerate(theme_collections, start=1):
        years.update(coll.extra_fields["osc:years"])
        number_of_products += coll.extra_fields["osc:numberOfProducts"]
        number_of_variables += coll.extra_fields["osc:numberOfVariables"]

    catalog.extra_fields = {
        "osc:numberOfProducts": number_of_products,
        "osc:numberOfProjects": len(project_items),
        "osc:numberOfVariables": number_of_variables,
        "osc:numberOfThemes": i,
        "osc:years": sorted(years),
    }

    metrics = {
        "id": catalog.id,
        "summary": {
            "years": catalog.extra_fields["osc:years"],
            "numberOfProducts": catalog.extra_fields["osc:numberOfProducts"],
            "numberOfProjects": catalog.extra_fields["osc:numberOfProjects"],
            "numberOfVariables": catalog.extra_fields["osc:numberOfVariables"],
            "numberOfThemes": catalog.extra_fields["osc:numberOfThemes"],
        },
        "themes": [
            {
                "name": theme_coll.id,
                "description": theme_coll.description,
                "image": "...",
                "website": theme_coll.get_single_link(pystac.RelType.VIA).get_href(),
                # "technicalOfficer": theme_coll.extra_fields["osc:technical_officer"]["name"],
                "summary": {
                    "years": theme_coll.extra_fields["osc:years"],
                    "numberOfProducts": theme_coll.extra_fields["osc:numberOfProducts"],
                    "numberOfProjects": theme_coll.extra_fields["osc:numberOfProjects"],
                    "numberOfVariables": theme_coll.extra_fields["osc:numberOfVariables"],
                },
                "variables": [
                    {
                        "name": var_coll.id,
                        "description": var_coll.description,
                        "summary": {
                            "years": var_coll.extra_fields["osc:years"],
                            "numberOfProducts": var_coll.extra_fields["osc:numberOfProducts"],
                        }
                    }
                    for var_coll in theme_coll.get_collections()
                ]
            }
            for theme_coll in catalog.get_collections()
        ]
    }

    os.makedirs(out_dir)
    os.chdir(out_dir)

    with open("metrics.json", "w") as f:
        json.dump(metrics, f, indent=4)

    catalog.add_link(pystac.Link(pystac.RelType.ALTERNATE, "./metrics.json", "application/json"))

    # catalog.describe(True)

    # catalog.save(pystac.CatalogType.SELF_CONTAINED, dest_href='dist/')

    # create the output directory and switch there to allow a clean build


    catalog.normalize_and_save(
        "",
        # pystac.CatalogType.ABSOLUTE_PUBLISHED,
        pystac.CatalogType.SELF_CONTAINED,
        # strategy=pystac.layout.TemplateLayoutStrategy(
        #     collection_template="${osc:type}s/${id}.json",
        #     item_template="${osc:type}s/${id}.json"
        # )

        strategy=pystac.layout.CustomLayoutStrategy(
            collection_func=lambda coll, parent_dir, is_root: f"{coll.extra_fields['osc:type'].lower()}s/{slugify(coll.id)}.json",
            item_func=lambda item, parent_dir: f"{item.properties['osc:type'].lower()}s/{item.id}.json",
        )
    )


if __name__ == "__main__":
    main()
