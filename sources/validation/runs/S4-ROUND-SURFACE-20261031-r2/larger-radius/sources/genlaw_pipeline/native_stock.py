"""Construct a native CAM stock from declared incoming bounds, independent of CAD."""


def bounds(shape):
    b = shape.BoundBox
    return {'min': [b.XMin, b.YMin, b.ZMin], 'max': [b.XMax, b.YMax, b.ZMax]}


def set_box(job, declared):
    import FreeCAD as App
    import Path.Main.Stock as Stock
    low, high = declared['min'], declared['max']
    old = job.Stock
    stock = Stock.CreateBox(job, extent=App.Vector(*[b-a for a,b in zip(low, high)]),
                            placement=App.Placement(App.Vector(*low), App.Rotation()))
    job.Stock = stock
    job.Document.removeObject(old.Name)
    job.Document.recompute()
    if any(abs(bounds(stock.Shape)[k][i] - declared[k][i]) > 1e-8
           for k in ['min', 'max'] for i in range(3)):
        raise ValueError('Native CAM stock disagrees with incoming resource bounds')
    return {'construction': 'FreeCAD Stock.CreateBox from declared incoming bounds',
            'bounds_mm': bounds(stock.Shape), 'stock_type': str(Stock.StockType.FromStock(stock))}
