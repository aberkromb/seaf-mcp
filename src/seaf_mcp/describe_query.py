"""Meta-JSONata builder for describe_query_result.

Own implementation (helper's GPLv3 code is not copied); uses only syntax
confirmed on a live SEAF portal:
- index filter [[a..b]] for sampling
- object/array branching via ternary; closures capture outer variables
- $reduce + $append to flatten arrays of key lists (no auto-flatten)
- multi-statement function bodies wrapped in ( ... )
- the WHOLE meta-query is a single ( ... ) block: every ';' is inside parens
"""

SAMPLE_SIZE = 100
EXAMPLE_COUNT = 3
EXAMPLE_MAX_CHARS = 1500


def build_analyze_query(query: str, sample_size: int = SAMPLE_SIZE,
                        example_count: int = EXAMPLE_COUNT,
                        example_max_chars: int = EXAMPLE_MAX_CHARS) -> str:
    """Wrap the user query in an analyzing meta-query.

    The result object: result_type (array|object|scalar types|missing),
    count (over the FULL result), analyzed (sample size actually used),
    fields (name + present_in k/n over the sample), examples (capped),
    top_keys (for object results), value (for scalars).
    """
    q = query.strip()
    last_sample = sample_size - 1
    last_example = example_count - 1
    return (
        f'($r := ({q}); '
        f'$t := $exists($r) ? $type($r) : "missing"; '
        f'$isArr := $t = "array"; '
        f'$isObj := $t = "object"; '
        f'$isScalar := $t in ["string", "number", "boolean", "null"]; '
        f'$n := $isArr ? $count($r) : ($isObj ? $count($keys($r)) : 0); '
        f'$sample := $isArr ? $r[[0..{last_sample}]] '
        f': ($isObj ? $map($keys($r)[[0..{last_sample}]], function($k){{$lookup($r, $k)}}) : []); '
        f'$ns := $count($sample); '
        f'$objSample := $filter($sample, function($e){{$type($e) = "object"}}); '
        f'$allKeys := $distinct($reduce($map($objSample, function($e){{$keys($e)}}), '
        f'function($acc, $ks){{$append($acc, $ks)}}, [])); '
        f'$fields := $map($allKeys, function($f){{{{"field": $f, '
        f'"present_in": $string($count($filter($objSample, function($e){{$exists($lookup($e, $f))}})))'
        f' & "/" & $string($ns)}}}}); '
        f'$examples := $map($sample[[0..{last_example}]], function($e){{('
        f'$s := $string($e); '
        f'$length($s) > {example_max_chars} ? $substring($s, 0, {example_max_chars}) & "…[cut]" : $e'
        f')}}); '
        f'{{"result_type": $t, "count": $n, "analyzed": $ns, '
        f'"schema_note": "schema is inferred from the first " & $string($ns) & " element(s) of the result", '
        f'"top_keys": ($isObj ? $keys($r)[[0..{last_example}]] : []), '
        f'"fields": $fields, "examples": $examples, '
        f'"value": ($isScalar ? $r : null)}})'
    )
