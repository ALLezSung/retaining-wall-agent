"""Read Agent Skills YAML frontmatter without executing constructors or aliases."""
import re
import yaml


class MetadataLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError("技能元数据不支持 YAML 别名。")
        return super().compose_node(parent, index)


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError("技能元数据含重复或非字符串字段。")
        result[key] = loader.construct_object(value_node)
    return result


MetadataLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def read_metadata(path, *, legacy_name=False):
    # Read only the bounded header at discovery; body/resources remain on demand.
    lines, size = [], 0
    with path.open(encoding="utf-8-sig") as stream:
        if stream.readline(65537).rstrip("\r\n") != "---":
            raise ValueError(f"缺少技能元数据：{path}")
        for line in stream:
            size += len(line.encode("utf-8"))
            if size > 65536:
                raise ValueError(f"技能元数据过大：{path}")
            if line.rstrip("\r\n") == "---":
                break
            lines.append(line)
        else:
            raise ValueError(f"技能元数据未闭合：{path}")
    try:
        data = yaml.load("".join(lines), Loader=MetadataLoader)
    except (yaml.YAMLError, ValueError, RecursionError) as exc:
        raise ValueError(f"无效技能元数据：{path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"技能元数据必须是映射：{path}")
    name, description = data.get("name"), data.get("description")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"技能名称缺失：{path}")
    if not legacy_name and (len(name) > 64
                            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                            or name != path.parent.name):
        raise ValueError(f"技能 name 必须与目录同名，使用小写字母、数字和连字符：{path}")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise ValueError(f"技能说明缺失或超长：{path}")
    extensions = data.get("metadata", {})
    if not isinstance(extensions, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in extensions.items()):
        raise ValueError(f"技能 metadata 必须是字符串映射：{path}")
    return data
