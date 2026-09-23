import { NextResponse } from "next/server";
import demo from "@/data/demo.json";

export function GET() {
  return NextResponse.json(demo);
}
